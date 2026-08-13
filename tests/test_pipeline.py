from __future__ import annotations

import argparse
import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).parents[1] / "scripts/run.py"
SPEC = importlib.util.spec_from_file_location("xhs_hot_pipeline", SCRIPT)
pipeline = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(pipeline)


class CountAndTimeTests(unittest.TestCase):
    def test_parse_count_units(self) -> None:
        self.assertEqual(pipeline.parse_count("1.2万+"), 12000)
        self.assertEqual(pipeline.parse_count("3k"), 3000)
        self.assertEqual(pipeline.parse_count("2,345"), 2345)
        self.assertIsNone(pipeline.parse_count("赞"))

    def test_parse_relative_and_short_date(self) -> None:
        snapshot = datetime(2026, 8, 11, tzinfo=timezone.utc)
        self.assertEqual(pipeline.parse_datetime("3天前", snapshot), snapshot - timedelta(days=3))
        self.assertEqual(pipeline.parse_datetime("08-01", snapshot), datetime(2026, 8, 1, tzinfo=timezone.utc))
        self.assertEqual(pipeline.parse_datetime("12-31", snapshot), datetime(2025, 12, 31, tzinfo=timezone.utc))

    def test_percentile_ties(self) -> None:
        self.assertEqual(pipeline.percentile([1.0, 2.0, 2.0, None]), [0.0, 0.75, 0.75, None])

    def test_missing_time_reweights_hot_score(self) -> None:
        rows = [
            {"keyword": "测试", "rrf_score": 0.1, "likes": 1, "collects": 1, "comments": 1, "shares": 1, "age_days": None},
            {"keyword": "测试", "rrf_score": 0.2, "likes": 10, "collects": 10, "comments": 10, "shares": 10, "age_days": None},
        ]
        pipeline.score_posts(rows)
        self.assertEqual(rows[0]["hot_score"], 0.0)
        self.assertEqual(rows[1]["hot_score"], 100.0)
        self.assertIsNone(rows[0]["velocity_score"])


class PipelineIntegrationTests(unittest.TestCase):
    def test_browseract_error_payload_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "extraction failed"):
            pipeline.decode_browseract_payload(json.dumps({"error": True, "message": "login required"}))

    def test_missing_sort_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pipeline.init_run(argparse.Namespace(
                keywords="测试", time_window="6m", note_type="all", sampling_profile="light",
                output_dir=root, run_id="missing-sort", analyze_covers=False,
            ))
            with self.assertRaisesRegex(ValueError, "Missing BrowserAct feeds"):
                pipeline.fuse_candidates(argparse.Namespace(run_dir=root / "missing-sort"))

    def test_invalid_mediacrawler_path_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pipeline.init_run(argparse.Namespace(
                keywords="测试", time_window="6m", note_type="all", sampling_profile="light",
                output_dir=root, run_id="bad-media", analyze_covers=False,
            ))
            with mock.patch.dict("os.environ", {"MEDIACRAWLER_DIR": str(root / "missing")}, clear=False):
                with self.assertRaisesRegex(FileNotFoundError, "Invalid MediaCrawler"):
                    pipeline.crawl(argparse.Namespace(run_dir=root / "bad-media", phase="details", print_command=False))

    def test_fixture_pipeline_and_privacy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            init_args = argparse.Namespace(
                keywords="AI编程",
                time_window="6m",
                note_type="all",
                sampling_profile="light",
                output_dir=root,
                run_id="fixture-run",
                analyze_covers=False,
            )
            pipeline.init_run(init_args)
            run_dir = root / "fixture-run"
            now_ms = int((datetime.now(timezone.utc) - timedelta(days=10)).timestamp() * 1000)
            base_items = [
                {"id": "n1", "xsec_token": "secret-one", "note_url": "https://www.xiaohongshu.com/explore/n1?xsec_token=secret-one&xsec_source=pc_search", "type": "normal", "title": "5步搞定AI编程", "liked_count": "1.2万", "collected_count": "5000", "comment_count": "300", "shared_count": "100", "author_nickname": "作者甲"},
                {"id": "n2", "xsec_token": "secret-two", "note_url": "https://www.xiaohongshu.com/explore/n2?xsec_token=secret-two&xsec_source=pc_search", "type": "video", "title": "为什么AI编程总失败？", "liked_count": "8000", "collected_count": "1000", "comment_count": "600", "shared_count": "80", "author_nickname": "作者乙"},
                {"id": "n3", "xsec_token": "secret-three", "note_url": "https://www.xiaohongshu.com/explore/n3?xsec_token=secret-three&xsec_source=pc_search", "type": "normal", "title": "AI编程实测复盘", "liked_count": "3000", "collected_count": "2000", "comment_count": "100", "shared_count": "50", "author_nickname": "作者丙"},
            ]
            orders = {
                "popularity_descending": base_items,
                "collect_descending": [base_items[0], base_items[2], base_items[1]],
                "comment_descending": [base_items[1], base_items[0], base_items[2]],
            }
            for sort_name, items in orders.items():
                feed_file = root / f"{sort_name}.json"
                feed_file.write_text(json.dumps({"items": items, "has_more": False}, ensure_ascii=False), encoding="utf-8")
                pipeline.record_feed(argparse.Namespace(run_dir=run_dir, keyword="AI编程", sort=sort_name, input=feed_file))
            pipeline.fuse_candidates(argparse.Namespace(run_dir=run_dir))

            details_dir = run_dir / "raw/mediacrawler/details/xhs/jsonl"
            comments_dir = run_dir / "raw/mediacrawler/comments/xhs/jsonl"
            details_dir.mkdir(parents=True)
            comments_dir.mkdir(parents=True)
            notes = [
                {"note_id": "n1", "type": "normal", "title": "5步搞定AI编程", "desc": "1. 先定义问题\n2. 再运行测试", "time": now_ms, "nickname": "作者甲", "liked_count": "1.2万", "collected_count": "5000", "comment_count": "300", "share_count": "100", "tag_list": "AI编程,效率", "image_list": "a,b,c", "xsec_token": "must-not-leak", "creator_hash": "must-not-leak"},
                {"note_id": "n2", "type": "video", "title": "为什么AI编程总失败？", "desc": "常见错误和修复方法", "time": now_ms, "nickname": "作者乙", "liked_count": "8000", "collected_count": "1000", "comment_count": "600", "share_count": "80", "tag_list": "AI编程,避坑"},
                {"note_id": "n3", "type": "normal", "title": "AI编程实测复盘", "desc": "我试过三个工具", "time": now_ms, "nickname": "作者丙", "liked_count": "3000", "collected_count": "2000", "comment_count": "100", "share_count": "50", "tag_list": "AI编程,实测"},
            ]
            pipeline.write_jsonl(details_dir / "detail_contents_fixture.jsonl", notes)
            pipeline.write_jsonl(comments_dir / "detail_comments_fixture.jsonl", [
                {"comment_id": "c1", "note_id": "n1", "content": "请问新手怎么开始？", "like_count": "12", "create_time": now_ms, "nickname": "评论者", "creator_hash": "hidden"}
            ])
            pipeline.finalize(argparse.Namespace(run_dir=run_dir))

            expected = ["report.md", "ranked_posts.csv", "posts.jsonl", "comments.jsonl", "run_manifest.json"]
            for name in expected:
                self.assertTrue((run_dir / name).is_file(), name)
            posts_text = (run_dir / "posts.jsonl").read_text(encoding="utf-8")
            comments_text = (run_dir / "comments.jsonl").read_text(encoding="utf-8")
            self.assertNotIn("xsec_token", posts_text)
            self.assertNotIn("must-not-leak", posts_text)
            self.assertNotIn("评论者", comments_text)
            self.assertNotIn("creator_hash", comments_text)
            self.assertIn("https://www.xiaohongshu.com/explore/n1", posts_text)
            self.assertFalse((run_dir / ".work/candidates.private.jsonl").exists())
            self.assertEqual(len(pipeline.read_jsonl(run_dir / "posts.jsonl")), 3)
            self.assertEqual(len(pipeline.read_jsonl(run_dir / "comments.jsonl")), 1)


if __name__ == "__main__":
    unittest.main()
