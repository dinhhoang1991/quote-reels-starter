#!/usr/bin/env python3
"""Render clip rồi đăng Facebook Page. Có thể lấy clip kế tiếp từ hàng chờ."""

from __future__ import annotations

import argparse
import json
import os
from contextlib import nullcontext
from pathlib import Path

from config import load_config, root
from jobqueue import fail as queue_fail
from jobqueue import move as queue_move
from jobqueue import next_pending, queue_lock
from logutil import log, remaining_quota
from make_video import build
from schema import load_clip
from upload_facebook import caption_from_json, load_env, upload_reel

ROOT = root()
DEFAULT_STALE_LOCK_SECONDS = 3600.0


def describe_error(exc: BaseException) -> str:
    """Mô tả ngắn gọn lỗi để ghi vào job trong failed/.

    @param exc lỗi bắt được từ bước render/upload
    @returns chuỗi người vận hành đọc được
    """
    if isinstance(exc, SystemExit):
        return str(exc.code) if exc.code else "SystemExit"
    return f"{type(exc).__name__}: {exc}"


def publish_one(
    json_path: Path,
    footage: Path | None,
    music: Path | None,
    voice: Path | None,
    state: str,
    skip_upload: bool,
    force: bool,
) -> None:
    load_env(ROOT / ".env")
    data = load_clip(json_path)
    video = build(json_path, footage, music, voice)
    log(f"video: {video}")
    if skip_upload:
        return

    page_id = os.getenv("FB_PAGE_ID", "").strip()
    token = os.getenv("FB_PAGE_ACCESS_TOKEN", "").strip()
    version = os.getenv("FB_API_VERSION", "v26.0")
    tags = os.getenv("DEFAULT_HASHTAGS", "")
    if not page_id or not token:
        raise SystemExit("Đã render xong nhưng chưa có token. Điền .env rồi chạy src/upload_facebook.py")

    log(f"quota còn {remaining_quota()} Reels trong 24h")
    title = " ".join(data.get("title", "").split())
    caption = caption_from_json(data, tags)
    result = upload_reel(
        video,
        page_id,
        token,
        caption,
        title,
        state.upper(),
        version,
        clip_id=data["id"],
        force=force,
        clip=data,
    )
    log(f"reel: {result.get('reel_url')}")
    print(json.dumps(result, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="Render + upload Facebook Reels")
    parser.add_argument("--json", default="", help="File JSON cụ thể")
    parser.add_argument("--queue", action="store_true", help="Lấy clip pending đầu tiên")
    parser.add_argument("--footage", default="")
    parser.add_argument("--music", default="")
    parser.add_argument("--voice", default="")
    parser.add_argument("--state", default=os.getenv("FB_DEFAULT_STATE", "PUBLISHED"))
    parser.add_argument("--skip-upload", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    json_path: Path | None = Path(args.json) if args.json else None
    from_queue = bool(args.queue or json_path is None)
    cfg = load_config()
    stale = float(cfg.queue.get("stale_lock_seconds", DEFAULT_STALE_LOCK_SECONDS))

    # Khoá trước khi đọc pending: hai cron chạy chồng sẽ lấy trùng 1 clip.
    with queue_lock(stale) if from_queue else nullcontext():
        if from_queue:
            json_path = next_pending()
            if json_path is None:
                raise SystemExit(
                    "Hàng chờ trống. python3 src/jobqueue.py add data/samples/clip_001.json"
                )
            log(f"queue: {json_path}")

        try:
            publish_one(
                json_path,
                Path(args.footage) if args.footage else None,
                Path(args.music) if args.music else None,
                Path(args.voice) if args.voice else None,
                args.state,
                args.skip_upload,
                args.force,
            )
        except (SystemExit, Exception) as exc:
            # Lỗi ffmpeg (CalledProcessError) không phải SystemExit: không bắt ở đây thì
            # job nằm mãi trong pending/ và cron lặp lại đúng clip lỗi mỗi ngày.
            if from_queue:
                dest = queue_fail(json_path, describe_error(exc))
                log(f"lỗi, moved to {dest} (chi tiết ở _queue.last_error)")
            raise
        if from_queue:
            dest = queue_move(json_path, "done")
            log(f"moved to {dest}")


if __name__ == "__main__":
    main()
