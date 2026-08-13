#!/usr/bin/env python3
"""Deterministic pipeline for small-sample Xiaohongshu hot-post research."""

from __future__ import annotations

import argparse
import bisect
import csv
import hashlib
import json
import math
import os
import re
import shutil
import statistics
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit


DEFAULT_OUTPUT_ROOT = Path("/Users/xiyu/Desktop/self_media/xhs-hot-post-analysis")
DEFAULT_MEDIACRAWLER_DIR = Path("/Users/xiyu/Documents/coding/github/MediaCrawler")
SORTS = ("popularity_descending", "collect_descending", "comment_descending")
METRIC_WEIGHTS = {"likes": 0.35, "collects": 0.30, "comments": 0.20, "shares": 0.15}
SCORING_VERSION = "xhs-relative-hot-v1"
PROFILE_LIMITS = {
    "light": {"candidate": 40, "detail": 20, "comment_notes": 5, "comments_per_note": 20},
    "balanced": {"candidate": 80, "detail": 30, "comment_notes": 10, "comments_per_note": 50},
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).isoformat() if dt else None


def slugify(value: str, limit: int = 36) -> str:
    ascii_part = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    if ascii_part:
        return ascii_part[:limit]
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:10]
    return f"keyword-{digest}"


def parse_keywords(raw: str) -> list[str]:
    values = [part.strip() for part in re.split(r"[,，\n]", raw) if part.strip()]
    return list(dict.fromkeys(values))


def read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    temp.replace(path)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL at {path}:{line_number}: {exc}") from exc
            if isinstance(value, dict):
                rows.append(value)
    return rows


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    temp.replace(path)


def parse_count(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return max(0, int(value))
    text = str(value).strip().lower().replace(",", "").replace("+", "")
    if not text:
        return None
    multipliers = {"亿": 100_000_000, "万": 10_000, "w": 10_000, "千": 1_000, "k": 1_000}
    multiplier = 1
    for suffix, factor in multipliers.items():
        if suffix in text:
            multiplier = factor
            text = text.replace(suffix, "")
            break
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    return max(0, int(float(match.group()) * multiplier))


def parse_datetime(value: Any, snapshot: datetime) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.strip().isdigit()):
        number = float(value)
        if number > 10_000_000_000:
            number /= 1000
        if number > 1_000_000_000:
            try:
                return datetime.fromtimestamp(number, tz=timezone.utc)
            except (ValueError, OSError):
                return None
    text = str(value).strip()
    relative = re.fullmatch(r"(\d+)\s*(分钟|小时|天)前", text)
    if relative:
        amount = int(relative.group(1))
        unit = relative.group(2)
        delta = {"分钟": timedelta(minutes=amount), "小时": timedelta(hours=amount), "天": timedelta(days=amount)}[unit]
        return snapshot - delta
    if text == "昨天":
        return snapshot - timedelta(days=1)
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            parsed = datetime.strptime(text, pattern).replace(tzinfo=timezone.utc)
            return parsed
        except ValueError:
            pass
    short = re.fullmatch(r"(\d{1,2})-(\d{1,2})", text)
    if short:
        parsed = datetime(snapshot.year, int(short.group(1)), int(short.group(2)), tzinfo=timezone.utc)
        if parsed > snapshot + timedelta(days=1):
            parsed = parsed.replace(year=parsed.year - 1)
        return parsed
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).astimezone(timezone.utc)
    except ValueError:
        return None


def clean_note_url(note_id: str, value: Any = None) -> str:
    if note_id:
        return f"https://www.xiaohongshu.com/explore/{note_id}"
    if value:
        split = urlsplit(str(value))
        return urlunsplit((split.scheme, split.netloc, split.path, "", ""))
    return ""


def split_tags(value: Any) -> list[str]:
    if isinstance(value, list):
        tags = [item.get("name", "") if isinstance(item, dict) else str(item) for item in value]
    else:
        tags = re.split(r"[,，#]", str(value or ""))
    return list(dict.fromkeys(tag.strip() for tag in tags if tag and tag.strip()))


def coalesce(*values: Any) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return None


def image_count(value: Any) -> int:
    if isinstance(value, list):
        return len(value)
    return len([item for item in str(value or "").split(",") if item.strip()])


def decode_browseract_payload(raw: str) -> dict[str, Any]:
    value: Any = json.loads(raw)
    for _ in range(4):
        if isinstance(value, str):
            value = json.loads(value)
            continue
        if isinstance(value, dict) and "items" not in value:
            for key in ("result", "value", "data", "output"):
                if key in value and isinstance(value[key], (str, dict)):
                    value = value[key]
                    break
            else:
                break
            continue
        break
    if not isinstance(value, dict):
        raise ValueError("BrowserAct output must resolve to a JSON object")
    if value.get("error"):
        raise ValueError(f"BrowserAct extraction failed: {value.get('message', 'unknown error')}")
    if not isinstance(value.get("items"), list):
        raise ValueError("BrowserAct output has no items array")
    return value


def percentile(values: list[float | None]) -> list[float | None]:
    present = sorted(float(value) for value in values if value is not None)
    if not present:
        return [None] * len(values)
    if len(present) == 1:
        return [1.0 if value is not None else None for value in values]
    output: list[float | None] = []
    for value in values:
        if value is None:
            output.append(None)
            continue
        left = bisect.bisect_left(present, float(value))
        right = bisect.bisect_right(present, float(value)) - 1
        output.append(((left + right) / 2) / (len(present) - 1))
    return output


def weighted_available(values: dict[str, float | None], weights: dict[str, float]) -> float | None:
    denominator = sum(weights[key] for key, value in values.items() if value is not None)
    if not denominator:
        return None
    return sum(float(value) * weights[key] for key, value in values.items() if value is not None) / denominator


def manifest_path(run_dir: Path) -> Path:
    return run_dir / "run_manifest.json"


def load_manifest(run_dir: Path) -> dict[str, Any]:
    path = manifest_path(run_dir)
    if not path.is_file():
        raise FileNotFoundError(f"Missing run manifest: {path}")
    return read_json(path)


def command_version(command: list[str], cwd: Path | None = None) -> str | None:
    try:
        result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    lines = (result.stdout or result.stderr).strip().splitlines()
    return lines[0] if lines else None


def init_run(args: argparse.Namespace) -> int:
    keywords = parse_keywords(args.keywords)
    if not keywords:
        raise ValueError("--keywords must contain at least one keyword")
    if args.analyze_covers:
        raise ValueError("Cover analysis is reserved but unsupported in v1; omit --analyze-covers")
    started = utc_now()
    run_id = args.run_id or f"{started:%Y%m%d-%H%M%S}-{slugify(keywords[0])}"
    root = args.output_dir or DEFAULT_OUTPUT_ROOT
    run_dir = root / run_id
    if run_dir.exists() and any(run_dir.iterdir()):
        raise FileExistsError(f"Run directory is not empty: {run_dir}")
    for path in (run_dir / ".work", run_dir / "raw/mediacrawler/details", run_dir / "raw/mediacrawler/comments"):
        path.mkdir(parents=True, exist_ok=True)
    media_dir = Path(os.environ.get("MEDIACRAWLER_DIR", str(DEFAULT_MEDIACRAWLER_DIR))).resolve()
    manifest = {
        "run_id": run_id,
        "started_at": iso(started),
        "finished_at": None,
        "purpose": "non-commercial learning and research",
        "parameters": {
            "keywords": keywords,
            "time_window": args.time_window,
            "note_type": args.note_type,
            "sampling_profile": args.sampling_profile,
            "analyze_covers": bool(args.analyze_covers),
        },
        "scoring_version": SCORING_VERSION,
        "tools": {
            "browser_act": command_version([shutil.which("browser-act") or "browser-act", "--version"]),
            "mediacrawler_dir": str(media_dir),
            "mediacrawler_commit": command_version(["git", "rev-parse", "HEAD"], media_dir),
        },
        "counts": {},
        "failures": [],
    }
    write_json(manifest_path(run_dir), manifest)
    print(json.dumps({"run_dir": str(run_dir), "run_id": run_id}, ensure_ascii=False))
    return 0


def record_feed(args: argparse.Namespace) -> int:
    if args.sort not in SORTS:
        raise ValueError(f"Unsupported sort: {args.sort}")
    run_dir = args.run_dir.resolve()
    manifest = load_manifest(run_dir)
    if args.keyword not in manifest["parameters"]["keywords"]:
        raise ValueError(f"Keyword not declared in manifest: {args.keyword}")
    raw = args.input.read_text(encoding="utf-8") if args.input else sys.stdin.read()
    payload = decode_browseract_payload(raw.strip())
    record = {
        "keyword": args.keyword,
        "sort": args.sort,
        "collected_at": iso(utc_now()),
        "has_more": bool(payload.get("has_more")),
        "items": payload["items"],
    }
    path = run_dir / ".work/browseract_feeds.private.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(json.dumps({"recorded": len(record["items"]), "keyword": args.keyword, "sort": args.sort}, ensure_ascii=False))
    return 0


def fuse_candidates(args: argparse.Namespace) -> int:
    run_dir = args.run_dir.resolve()
    manifest = load_manifest(run_dir)
    profile = PROFILE_LIMITS[manifest["parameters"]["sampling_profile"]]
    records = read_jsonl(run_dir / ".work/browseract_feeds.private.jsonl")
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for record in records:
        latest[(str(record.get("keyword")), str(record.get("sort")))] = record
    missing = [
        f"{keyword}:{sort_name}"
        for keyword in manifest["parameters"]["keywords"]
        for sort_name in SORTS
        if (keyword, sort_name) not in latest
    ]
    if missing:
        raise ValueError("Missing BrowserAct feeds: " + ", ".join(missing))
    all_candidates: list[dict[str, Any]] = []
    per_keyword_counts: dict[str, int] = {}
    for keyword in manifest["parameters"]["keywords"]:
        candidates: dict[str, dict[str, Any]] = {}
        for sort_name in SORTS:
            items = latest[(keyword, sort_name)].get("items", [])[:40]
            seen_in_list: set[str] = set()
            for rank, item in enumerate(items, 1):
                note_id = str(item.get("id") or item.get("note_id") or "").strip()
                if not note_id or note_id in seen_in_list:
                    continue
                seen_in_list.add(note_id)
                current = candidates.setdefault(note_id, {"keyword": keyword, "note_id": note_id, "native_ranks": {}, "rrf_score": 0.0})
                current["native_ranks"][sort_name] = rank
                current["rrf_score"] += 1.0 / (60 + rank)
                for key, value in item.items():
                    if value not in (None, "", []):
                        current[key] = value
        ranked = sorted(candidates.values(), key=lambda row: (-row["rrf_score"], row["note_id"]))[: profile["candidate"]]
        for rank, row in enumerate(ranked, 1):
            row["selection_rank"] = rank
            all_candidates.append(row)
        per_keyword_counts[keyword] = len(ranked)
    write_jsonl(run_dir / ".work/candidates.private.jsonl", all_candidates)
    manifest["counts"]["candidates_by_keyword"] = per_keyword_counts
    manifest["counts"]["candidate_rows"] = len(all_candidates)
    write_json(manifest_path(run_dir), manifest)
    print(json.dumps({"candidate_rows": len(all_candidates), "by_keyword": per_keyword_counts}, ensure_ascii=False))
    return 0


def candidate_urls(run_dir: Path, phase: str) -> tuple[list[str], int]:
    manifest = load_manifest(run_dir)
    profile = PROFILE_LIMITS[manifest["parameters"]["sampling_profile"]]
    limit = profile["detail"] if phase == "details" else profile["comment_notes"]
    candidates = read_jsonl(run_dir / ".work/candidates.private.jsonl")
    selected = [row for row in candidates if int(row.get("selection_rank", 999999)) <= limit]
    urls: list[str] = []
    seen: set[str] = set()
    for row in selected:
        note_id = str(row.get("note_id") or row.get("id") or "")
        url = str(row.get("note_url") or "")
        token = str(row.get("xsec_token") or "")
        if not url and note_id and token:
            url = f"https://www.xiaohongshu.com/explore/{note_id}?xsec_token={token}&xsec_source=pc_search"
        if note_id and url and "xsec_token=" in url and note_id not in seen:
            seen.add(note_id)
            urls.append(url)
    return urls, profile["comments_per_note"]


def crawl(args: argparse.Namespace) -> int:
    run_dir = args.run_dir.resolve()
    manifest = load_manifest(run_dir)
    media_dir = Path(os.environ.get("MEDIACRAWLER_DIR", manifest["tools"]["mediacrawler_dir"])).resolve()
    if not (media_dir / "main.py").is_file():
        raise FileNotFoundError(f"Invalid MediaCrawler directory: {media_dir}")
    urls, comments_per_note = candidate_urls(run_dir, args.phase)
    if not urls:
        raise ValueError("No candidates with valid xsec_token URLs; refresh BrowserAct feeds")
    output_dir = run_dir / f"raw/mediacrawler/{args.phase}"
    get_comments = args.phase == "comments"
    command = [
        shutil.which("uv") or "uv",
        "run",
        "main.py",
        "--platform", "xhs",
        "--lt", "qrcode",
        "--type", "detail",
        "--specified_id", ",".join(urls),
        "--get_comment", "yes" if get_comments else "no",
        "--get_sub_comment", "no",
        "--max_comments_count_singlenotes", str(comments_per_note),
        "--max_concurrency_num", "1",
        "--headless", "no",
        "--save_data_option", "jsonl",
        "--save_data_path", str(output_dir),
    ]
    if args.print_command:
        print(json.dumps(command, ensure_ascii=False))
        return 0
    result = subprocess.run(command, cwd=media_dir, check=False)
    manifest = load_manifest(run_dir)
    manifest.setdefault("crawl_runs", []).append(
        {"phase": args.phase, "finished_at": iso(utc_now()), "note_count": len(urls), "exit_code": result.returncode}
    )
    if result.returncode:
        manifest["failures"].append({"stage": f"mediacrawler_{args.phase}", "exit_code": result.returncode})
    write_json(manifest_path(run_dir), manifest)
    return result.returncode


def merge_nonempty(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key, value in source.items():
        if value not in (None, "", []):
            target[key] = value


def load_mediacrawler(run_dir: Path) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    details: dict[str, dict[str, Any]] = {}
    comments: list[dict[str, Any]] = []
    for path in sorted((run_dir / "raw/mediacrawler").rglob("*.jsonl")):
        for row in read_jsonl(path):
            if row.get("comment_id") or ("content" in row and "note_id" in row and "title" not in row):
                comments.append(row)
                continue
            note_id = str(row.get("note_id") or "")
            if note_id:
                merge_nonempty(details.setdefault(note_id, {}), row)
    return details, comments


def note_type(value: Any) -> str:
    text = str(value or "").lower()
    if text in {"normal", "图文", "普通笔记", "image", "image-text"}:
        return "normal"
    if text in {"video", "视频", "视频笔记"}:
        return "video"
    return text or "unknown"


def within_window(published: datetime | None, snapshot: datetime, window: str) -> bool:
    if not published or window == "all":
        return True
    days = {"1d": 1, "1w": 7, "6m": 183}[window]
    return published >= snapshot - timedelta(days=days) - timedelta(days=1)


def build_posts(run_dir: Path, manifest: dict[str, Any], details: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str], int]:
    snapshot = datetime.fromisoformat(manifest["started_at"])
    params = manifest["parameters"]
    profile = PROFILE_LIMITS[params["sampling_profile"]]
    candidates = [
        row for row in read_jsonl(run_dir / ".work/candidates.private.jsonl")
        if int(row.get("selection_rank", 999999)) <= profile["detail"]
    ]
    rows: list[dict[str, Any]] = []
    missing: list[str] = []
    outside = 0
    for candidate in candidates:
        note_id = str(candidate.get("note_id") or candidate.get("id") or "")
        detail = details.get(note_id, {})
        source = dict(candidate)
        merge_nonempty(source, detail)
        published = parse_datetime(source.get("time") or source.get("publish_date"), snapshot)
        if not within_window(published, snapshot, params["time_window"]):
            outside += 1
            continue
        normalized_type = note_type(source.get("type"))
        if params["note_type"] != "all" and normalized_type != params["note_type"]:
            continue
        if not detail:
            missing.append(note_id)
        likes = parse_count(coalesce(source.get("liked_count"), source.get("likedCount")))
        collects = parse_count(coalesce(source.get("collected_count"), source.get("collectedCount")))
        comment_count = parse_count(coalesce(source.get("comment_count"), source.get("commentCount")))
        shares = parse_count(coalesce(source.get("share_count"), source.get("shared_count"), source.get("shareCount")))
        age_days = max((snapshot - published).total_seconds() / 86400, 1.0) if published else None
        row = {
            "keyword": candidate["keyword"],
            "note_id": note_id,
            "note_url": clean_note_url(note_id, source.get("note_url")),
            "nickname": str(source.get("nickname") or source.get("author_nickname") or ""),
            "note_type": normalized_type,
            "title": str(source.get("title") or ""),
            "desc": str(source.get("desc") or ""),
            "published_at": iso(published),
            "age_days": round(age_days, 3) if age_days is not None else None,
            "tags": split_tags(source.get("tag_list") or source.get("tagList")),
            "image_count": image_count(source.get("image_list") or source.get("imageList")),
            "likes": likes,
            "collects": collects,
            "comments": comment_count,
            "shares": shares,
            "native_ranks": candidate.get("native_ranks", {}),
            "rrf_score": round(float(candidate.get("rrf_score", 0)), 8),
            "detail_status": "mediacrawler" if detail else "candidate_only",
        }
        denominator = max(likes or 0, 1)
        row["collect_like_ratio"] = round(collects / denominator, 4) if collects is not None else None
        row["comment_like_ratio"] = round(comment_count / denominator, 4) if comment_count is not None else None
        row["share_like_ratio"] = round(shares / denominator, 4) if shares is not None else None
        rows.append(row)
    return rows, sorted(set(missing)), outside


def score_posts(rows: list[dict[str, Any]]) -> None:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[row["keyword"]].append(row)
    for group in groups.values():
        native_pct = percentile([row["rrf_score"] for row in group])
        metric_pct: dict[str, list[float | None]] = {}
        velocity_pct: dict[str, list[float | None]] = {}
        for metric in METRIC_WEIGHTS:
            metric_pct[metric] = percentile([
                math.log1p(row[metric]) if row.get(metric) is not None else None for row in group
            ])
            velocity_pct[metric] = percentile([
                math.log1p(row[metric] / row["age_days"])
                if row.get(metric) is not None and row.get("age_days") is not None else None
                for row in group
            ])
        for index, row in enumerate(group):
            engagement = weighted_available({metric: metric_pct[metric][index] for metric in METRIC_WEIGHTS}, METRIC_WEIGHTS)
            velocity = weighted_available({metric: velocity_pct[metric][index] for metric in METRIC_WEIGHTS}, METRIC_WEIGHTS)
            native = native_pct[index]
            row["native_percentile"] = round(100 * native, 2) if native is not None else None
            row["engagement_score"] = round(100 * engagement, 2) if engagement is not None else None
            row["velocity_score"] = round(100 * velocity, 2) if velocity is not None else None
            if row.get("age_days") is None:
                final = weighted_available({"native": native, "engagement": engagement}, {"native": 0.5, "engagement": 0.5})
            else:
                final = weighted_available(
                    {"native": native, "engagement": engagement, "velocity": velocity},
                    {"native": 0.4, "engagement": 0.4, "velocity": 0.2},
                )
            row["hot_score"] = round(100 * final, 2) if final is not None else None


def normalize_comments(raw_comments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in raw_comments:
        note_id = str(row.get("note_id") or "")
        content = str(row.get("content") or "").strip()
        if not note_id or not content:
            continue
        raw_key = str(row.get("comment_id") or f"{note_id}|{content}|{row.get('create_time', '')}")
        key = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:16]
        if key in seen:
            continue
        seen.add(key)
        parent = str(row.get("parent_comment_id") or "")
        created = parse_datetime(row.get("create_time"), utc_now())
        output.append({
            "comment_key": key,
            "note_id": note_id,
            "content": content,
            "like_count": parse_count(row.get("like_count")) or 0,
            "created_at": iso(created),
            "parent_comment_key": hashlib.sha256(parent.encode("utf-8")).hexdigest()[:16] if parent else None,
        })
    return output


def hook_categories(title: str) -> list[str]:
    categories: list[str] = []
    if re.search(r"\d+\s*(个|条|步|种|招|分钟|天)", title):
        categories.append("数字清单")
    if re.search(r"[?？]|为什么|怎么|如何|到底", title):
        categories.append("问题式")
    if re.search(r"亲测|实测|复盘|踩坑|我用|我做|终于", title):
        categories.append("经验复盘")
    if re.search(r"避坑|别再|不要|千万|劝你|慎入", title):
        categories.append("避坑提醒")
    if re.search(r"搞定|提升|省下|翻倍|逆袭|结果|真香", title):
        categories.append("结果承诺")
    return categories or ["直接陈述"]


def median_available(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [float(row[key]) for row in rows if row.get(key) is not None]
    return statistics.median(values) if values else None


def evidence_links(rows: list[dict[str, Any]], category: str, limit: int = 3) -> str:
    matched = [row for row in rows if category in hook_categories(row["title"])]
    matched.sort(key=lambda row: -(row.get("hot_score") or 0))
    return "、".join(f"[{row['title'] or row['note_id']}]({row['note_url']})" for row in matched[:limit]) or "无"


def render_report(manifest: dict[str, Any], posts: list[dict[str, Any]], comments: list[dict[str, Any]]) -> str:
    params = manifest["parameters"]
    lines = [
        "# 小红书热门帖数据分析",
        "",
        f"- 运行 ID：`{manifest['run_id']}`",
        f"- 关键词：{'、'.join(params['keywords'])}",
        f"- 时间范围：`{params['time_window']}`",
        f"- 样本：{len(posts)} 条关键词—笔记记录，{len(comments)} 条一级评论",
        "- 口径：小红书原生三榜融合 + 当前样本内相对热度分",
        "",
        "> 热度分只反映本次采样中的相对位置，不代表小红书全站排名，也不构成爆款承诺。",
        "",
    ]
    if not posts:
        lines.extend(["## 结果", "", "本次没有获得可分析的笔记。请检查登录、筛选条件、令牌和采集失败记录。", ""])
        return "\n".join(lines)
    for keyword in params["keywords"]:
        group = [row for row in posts if row["keyword"] == keyword]
        group.sort(key=lambda row: (-(row.get("hot_score") or -1), row["note_id"]))
        if not group:
            continue
        lines.extend([f"## {keyword}：Top 10", "", "| 排名 | 热度分 | 笔记 | 类型 | 赞 | 藏 | 评 | 分享 |", "|---:|---:|---|---|---:|---:|---:|---:|"])
        for rank, row in enumerate(group[:10], 1):
            title = (row["title"] or row["note_id"]).replace("|", "\\|")
            lines.append(
                f"| {rank} | {row.get('hot_score', '')} | [{title}]({row['note_url']}) | {row['note_type']} | "
                f"{row.get('likes') or 0} | {row.get('collects') or 0} | {row.get('comments') or 0} | {row.get('shares') or 0} |"
            )
        lengths = [len(row["title"]) for row in group if row["title"]]
        type_counts = Counter(row["note_type"] for row in group)
        hooks = Counter(category for row in group for category in hook_categories(row["title"]))
        tags = Counter(tag for row in group for tag in row.get("tags", []))
        lines.extend([
            "",
            "### 数据观察",
            "",
            f"- 标题长度中位数：{statistics.median(lengths) if lengths else '无数据'} 字；内容形式：" + "、".join(f"{key} {value} 篇" for key, value in type_counts.most_common()),
            f"- 收藏/点赞中位数：{median_available(group, 'collect_like_ratio') if median_available(group, 'collect_like_ratio') is not None else '无数据'}；评论/点赞中位数：{median_available(group, 'comment_like_ratio') if median_available(group, 'comment_like_ratio') is not None else '无数据'}。这些是互动结构，不是曝光转化率。",
            f"- 高频标签：{'、'.join(f'{tag}（{count}）' for tag, count in tags.most_common(10)) or '无'}。",
            "",
            "### 标题与结构线索",
            "",
        ])
        for category, count in hooks.most_common():
            lines.append(f"- 数据观察：{category}出现在 {count}/{len(group)} 篇；证据：{evidence_links(group, category)}。")
        numbered = [row for row in group if re.search(r"(^|\n)\s*(\d+[.、)]|[一二三四五六七八九十]+[、.])", row.get("desc", ""))]
        lines.append(f"- 数据观察：{len(numbered)}/{len(group)} 篇正文出现编号式结构。")
        lines.append("- 编辑推断：优先从高收藏比笔记提炼可保存的信息框架，再结合评论中的未解决问题设计新内容；不要直接复制标题或正文。")
        lines.append("")
    categories = {
        "问题求助": r"怎么|如何|哪里|什么|为什么|请问|求|吗[？?]?",
        "痛点困难": r"不会|失败|踩坑|焦虑|麻烦|太难|好难|贵",
        "反对质疑": r"但是|不一定|不行|没用|广告|质疑|骗人",
        "经验补充": r"我也|我用|亲测|试过|之前|后来",
    }
    lines.extend(["## 评论需求信号", ""])
    if comments:
        for label, pattern in categories.items():
            matched = [row for row in comments if re.search(pattern, row["content"], re.IGNORECASE)]
            supporting = sorted({row["note_id"] for row in matched})
            links = [row for row in posts if row["note_id"] in supporting][:3]
            evidence = "、".join(f"[{row['title'] or row['note_id']}]({row['note_url']})" for row in links) or "无"
            lines.append(f"- 数据观察：{label} {len(matched)}/{len(comments)} 条，涉及 {len(supporting)} 篇笔记；证据：{evidence}。")
    else:
        lines.append("- 本次没有获得评论样本，不能推断用户需求。")
    lines.extend([
        "",
        "## 方法与限制",
        "",
        "- 候选来自最多点赞、最多收藏、最多评论三种原生排序，并使用 Reciprocal Rank Fusion 合并。",
        "- 点赞、收藏、评论、分享先做 log1p 与样本内百分位，再结合互动速度计算相对热度分。",
        "- 搜索结果受账号、时间、地区和平台推荐影响；评论仅为前排笔记的小样本一级评论。",
        "- 缺少曝光与粉丝分母，因此不计算曝光率，也不做因果推断。",
        "- 第一版不下载或分析封面、图片和视频。",
        "",
    ])
    return "\n".join(lines)


def write_csv(path: Path, posts: list[dict[str, Any]]) -> None:
    fields = [
        "keyword", "hot_score", "native_percentile", "engagement_score", "velocity_score",
        "note_id", "note_url", "nickname", "note_type", "title", "published_at", "age_days",
        "likes", "collects", "comments", "shares", "collect_like_ratio", "comment_like_ratio",
        "share_like_ratio", "tags", "image_count", "detail_status",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for post in posts:
            row = {key: post.get(key) for key in fields}
            row["tags"] = ",".join(post.get("tags", []))
            writer.writerow(row)


def finalize(args: argparse.Namespace) -> int:
    run_dir = args.run_dir.resolve()
    manifest = load_manifest(run_dir)
    details, raw_comments = load_mediacrawler(run_dir)
    posts, missing, outside = build_posts(run_dir, manifest, details)
    score_posts(posts)
    posts.sort(key=lambda row: (row["keyword"], -(row.get("hot_score") or -1), row["note_id"]))
    comments = normalize_comments(raw_comments)
    write_jsonl(run_dir / "posts.jsonl", posts)
    write_jsonl(run_dir / "comments.jsonl", comments)
    write_csv(run_dir / "ranked_posts.csv", posts)
    manifest["finished_at"] = iso(utc_now())
    manifest["counts"].update({
        "post_rows": len(posts),
        "unique_notes": len({row["note_id"] for row in posts}),
        "comment_rows": len(comments),
        "details_loaded": len(details),
        "outside_window_dropped": outside,
    })
    if missing:
        manifest["failures"].append({"stage": "detail_merge", "missing_note_ids": missing})
    write_json(manifest_path(run_dir), manifest)
    report = render_report(manifest, posts, comments)
    (run_dir / "report.md").write_text(report, encoding="utf-8")
    private_files = [run_dir / ".work/browseract_feeds.private.jsonl", run_dir / ".work/candidates.private.jsonl"]
    for path in private_files:
        if path.exists():
            path.unlink()
    print(json.dumps({
        "run_dir": str(run_dir),
        "posts": len(posts),
        "comments": len(comments),
        "missing_details": len(missing),
        "outputs": ["report.md", "ranked_posts.csv", "posts.jsonl", "comments.jsonl", "run_manifest.json"],
    }, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Xiaohongshu hot-post analysis pipeline")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init", help="create an isolated run directory")
    init.add_argument("--keywords", required=True)
    init.add_argument("--time-window", choices=("1d", "1w", "6m", "all"), default="6m")
    init.add_argument("--note-type", choices=("all", "normal", "video"), default="all")
    init.add_argument("--sampling-profile", choices=tuple(PROFILE_LIMITS), default="balanced")
    init.add_argument("--output-dir", type=Path)
    init.add_argument("--run-id")
    init.add_argument("--analyze-covers", action="store_true", help="reserved; v1 rejects cover analysis")
    init.set_defaults(func=init_run)

    record = subparsers.add_parser("record-feed", help="record one BrowserAct search extraction")
    record.add_argument("--run-dir", type=Path, required=True)
    record.add_argument("--keyword", required=True)
    record.add_argument("--sort", choices=SORTS, required=True)
    record.add_argument("--input", type=Path)
    record.set_defaults(func=record_feed)

    fuse = subparsers.add_parser("fuse", help="deduplicate and fuse native rankings")
    fuse.add_argument("--run-dir", type=Path, required=True)
    fuse.set_defaults(func=fuse_candidates)

    crawl_parser = subparsers.add_parser("crawl", help="run MediaCrawler for selected candidates")
    crawl_parser.add_argument("--run-dir", type=Path, required=True)
    crawl_parser.add_argument("--phase", choices=("details", "comments"), required=True)
    crawl_parser.add_argument("--print-command", action="store_true")
    crawl_parser.set_defaults(func=crawl)

    final = subparsers.add_parser("finalize", help="normalize, score, and render outputs")
    final.add_argument("--run-dir", type=Path, required=True)
    final.set_defaults(func=finalize)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return int(args.func(args))
    except (FileNotFoundError, FileExistsError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": True, "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
