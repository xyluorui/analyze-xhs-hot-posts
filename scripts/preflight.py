#!/usr/bin/env python3
"""Check local dependencies without opening a browser or touching Xiaohongshu."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


DEFAULT_MEDIACRAWLER_DIR = Path("/Users/xiyu/Documents/coding/github/MediaCrawler")


def command_version(command: list[str]) -> str | None:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    output = (result.stdout or result.stderr).strip().splitlines()
    return output[0] if result.returncode == 0 and output else None


def main() -> int:
    parser = argparse.ArgumentParser(description="Preflight for the XHS hot-post analysis skill")
    parser.add_argument("--mediacrawler-dir", type=Path)
    args = parser.parse_args()

    media_dir = args.mediacrawler_dir or Path(
        os.environ.get("MEDIACRAWLER_DIR", str(DEFAULT_MEDIACRAWLER_DIR))
    )
    browser_act = shutil.which("browser-act")
    uv = shutil.which("uv")
    checks = {
        "usage": "personal non-commercial research (skill default)",
        "browser_act": {
            "path": browser_act,
            "version": command_version([browser_act, "--version"]) if browser_act else None,
        },
        "uv": {"path": uv, "available": bool(uv)},
        "mediacrawler": {
            "path": str(media_dir.resolve()),
            "main_py": (media_dir / "main.py").is_file(),
            "license": (media_dir / "LICENSE").is_file(),
        },
        "python": sys.version.split()[0],
    }
    errors: list[str] = []
    if not browser_act:
        errors.append("未找到 browser-act；安装：uv tool install browser-act-cli --python 3.12")
    if not uv:
        errors.append("未找到 uv")
    if not (media_dir / "main.py").is_file():
        errors.append(f"MediaCrawler 目录无效：{media_dir}")
    checks["ok"] = not errors
    checks["errors"] = errors
    print(json.dumps(checks, ensure_ascii=False, indent=2))
    return 0 if checks["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
