#!/usr/bin/env python3
"""Render clip rồi đăng Facebook Page trong 1 lệnh."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from make_video import build  # noqa: E402
from upload_facebook import caption_from_json, load_env, upload_reel  # noqa: E402


def main() -> None:
    load_env(ROOT / ".env")
    parser = argparse.ArgumentParser(description="Render + upload Facebook Reels")
    parser.add_argument("--json", required=True)
    parser.add_argument("--footage", default="")
    parser.add_argument("--music", default="")
    parser.add_argument("--voice", default="")
    parser.add_argument("--state", default=os.getenv("FB_DEFAULT_STATE", "PUBLISHED"))
    parser.add_argument("--skip-upload", action="store_true")
    args = parser.parse_args()

    json_path = Path(args.json)
    video = build(
        json_path,
        Path(args.footage) if args.footage else None,
        Path(args.music) if args.music else None,
        Path(args.voice) if args.voice else None,
    )
    print("Video:", video)

    if args.skip_upload:
        return

    page_id = os.getenv("FB_PAGE_ID", "").strip()
    token = os.getenv("FB_PAGE_ACCESS_TOKEN", "").strip()
    version = os.getenv("FB_API_VERSION", "v26.0")
    tags = os.getenv("DEFAULT_HASHTAGS", "")
    if not page_id or not token:
        raise SystemExit("Đã render xong nhưng chưa có token. Điền .env rồi chạy src/upload_facebook.py")

    import json

    data = json.loads(json_path.read_text(encoding="utf-8"))
    title = " ".join(data.get("title", "").split())
    caption = caption_from_json(data, tags)
    result = upload_reel(video, page_id, token, caption, title, args.state.upper(), version)
    print("Reel:", result.get("reel_url"))
    print(result)


if __name__ == "__main__":
    main()
