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
from notify import notify, notify_empty_queue, notify_publish_failure, notify_publish_success
from schema import load_clip
from upload_facebook import caption_from_json, load_env, upload_reel

ROOT = root()
DEFAULT_STALE_LOCK_SECONDS = 3600.0
CROSSPOST_TARGETS = ("youtube", "tiktok")


def resolve_targets(cli_value: str | None, cfg) -> list[str]:
    """Danh sách nền tảng cross-post: CLI ghi đè config `crosspost.targets`.

    @param cli_value giá trị --crosspost ('none' để tắt, rỗng/None để dùng config)
    @param cfg config đang dùng
    @returns danh sách target hợp lệ
    """
    if cli_value is None:
        raw = (cfg.get("crosspost", {}) or {}).get("targets") or []
    else:
        raw = [] if cli_value.strip().lower() in {"", "none", "off"} else cli_value.split(",")
    targets: list[str] = []
    for item in raw:
        name = str(item).strip().lower()
        if not name:
            continue
        if name not in CROSSPOST_TARGETS:
            raise SystemExit(
                f"crosspost không hỗ trợ {name!r} (chọn {' | '.join(CROSSPOST_TARGETS)} | none)"
            )
        if name not in targets:
            targets.append(name)
    return targets


def make_clip_cover(video: Path, data: dict) -> Path | None:
    """Sinh ảnh cover từ video (chỉ khi bật trong config và có ffmpeg).

    @param video file mp4 đã render
    @param data clip đã validate
    @returns đường dẫn ảnh cover hoặc None nếu tắt/lỗi
    """
    cfg = load_config()
    if not bool((cfg.get("cover", {}) or {}).get("enabled", True)):
        return None
    try:
        from make_video import make_cover

        return make_cover(video, data, cfg)
    except (SystemExit, Exception) as exc:
        log(f"cảnh báo: không tạo được ảnh cover ({exc})")
        return None


def crosspost(
    video: Path, data: dict, targets: list[str], cover: Path | None, caption: str,
    force: bool = False, dry_run: bool = False,
) -> dict[str, dict]:
    """Đăng thêm lên các nền tảng khác; lỗi từng nền tảng chỉ được ghi lại.

    Việc chống trùng và ghi log nằm trong từng uploader (`upload_youtube`/`upload_tiktok`)
    để chạy CLI trực tiếp cũng được bảo vệ.

    @param video file mp4 đã render
    @param data clip đã validate
    @param targets danh sách 'youtube' / 'tiktok'
    @param cover ảnh cover, có thể None
    @param caption caption dùng làm mô tả
    @param force đăng lại dù đã cross-post trước đó
    @param dry_run chỉ in kế hoạch request của từng nền tảng, không gọi mạng
    @returns {target: kết quả hoặc {"error": ...}}
    """
    cfg = load_config()
    tags = os.getenv("DEFAULT_HASHTAGS", "")
    cover_ms = int(round(float((cfg.get("cover", {}) or {}).get("at_seconds", 0) or 0) * 1000))
    clip_id = str(data.get("id", ""))
    results: dict[str, dict] = {}
    for target in targets:
        try:
            if target == "youtube":
                from upload_youtube import upload_clip as upload_youtube

                result = upload_youtube(
                    video, data, caption=caption, tags=tags, cover=cover,
                    clip_id=clip_id, force=force, dry_run=dry_run,
                )
            elif target == "tiktok":
                from upload_tiktok import upload_clip as upload_tiktok

                result = upload_tiktok(
                    video,
                    str(data.get("tiktok_caption") or caption or data.get("title", "")),
                    cover_timestamp_ms=cover_ms, clip_id=clip_id, force=force,
                    dry_run=dry_run,
                )
            else:
                result = {"error": f"nền tảng lạ: {target}"}
        except (SystemExit, Exception) as exc:
            result = {"error": str(exc)}
        results[target] = result
        if result.get("dry_run"):
            log(f"crosspost {target}: [dry-run] {result.get('url', '')}")
        elif result.get("error"):
            log(f"crosspost {target}: LỖI {result['error']}")
        elif result.get("skipped"):
            log(f"crosspost {target}: bỏ qua ({result.get('reason', 'đã đăng')})")
        else:
            log(f"crosspost {target}: {result.get('url') or result.get('publish_id') or 'ok'}")
    return results


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
    crosspost_targets: list[str] | None = None,
    no_first_comment: bool = False,
    dry_run: bool = False,
) -> None:
    """Render rồi đăng 1 clip; tuỳ chọn đăng thêm nền tảng khác.

    @param crosspost_targets nền tảng đăng thêm ('youtube'/'tiktok'), rỗng là chỉ Facebook
    @param no_first_comment bỏ qua comment đầu tiên
    @param dry_run render thật rồi in kế hoạch request của Facebook + cross-post, không gọi mạng
    """
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
    if not dry_run and (not page_id or not token):
        raise SystemExit("Đã render xong nhưng chưa có token. Điền .env rồi chạy src/upload_facebook.py")

    log(f"quota còn {remaining_quota()} Reels trong 24h")
    title = " ".join(data.get("title", "").split())
    caption = caption_from_json(data, tags)
    if dry_run:
        plan = {
            "dry_run": True,
            "clip": data["id"],
            "video": str(video),
            "facebook": upload_reel(
                video, page_id, token, caption, title, state.upper(), version,
                clip_id=data["id"], force=force, dry_run=True, clip=data,
                first_comment="" if no_first_comment else None,
            ),
        }
        if crosspost_targets and state.upper() == "PUBLISHED":
            cover = make_clip_cover(video, data)
            plan["cover"] = str(cover) if cover else ""
            plan["crosspost"] = crosspost(
                video, data, crosspost_targets, cover, caption, force=force, dry_run=True,
            )
        log("[dry-run] không gọi mạng — không upload, không đổi hàng chờ")
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return

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
        first_comment="" if no_first_comment else None,
    )
    log(f"reel: {result.get('reel_url')}")
    print(json.dumps(result, ensure_ascii=False, indent=2))

    notify_publish_success(data["id"], str(result.get("reel_url", "")))

    if crosspost_targets and state.upper() == "PUBLISHED":
        cover = make_clip_cover(video, data)
        log(f"5) crosspost: {', '.join(crosspost_targets)}"
            + (f" (cover {cover.name})" if cover else ""))
        crosspost(video, data, crosspost_targets, cover, caption, force=force)


def publish_clip(json_path: Path, args, targets: list[str], from_queue: bool) -> tuple[bool, str]:
    """Render và đăng 1 clip, xử lý lỗi theo hàng chờ.

    @param json_path clip cần đăng
    @param args tham số dòng lệnh
    @param targets nền tảng cross-post
    @param from_queue True nếu clip lấy từ pending/ (lỗi thì move sang failed/)
    @returns (thành công?, mô tả lỗi)
    """
    dry_run = bool(getattr(args, "dry_run", False))
    try:
        publish_one(
            json_path,
            Path(args.footage) if args.footage else None,
            Path(args.music) if args.music else None,
            Path(args.voice) if args.voice else None,
            args.state,
            args.skip_upload,
            args.force,
            crosspost_targets=targets,
            no_first_comment=args.no_first_comment,
            dry_run=dry_run,
        )
    except (SystemExit, Exception) as exc:
        # Lỗi ffmpeg (CalledProcessError) không phải SystemExit: không bắt ở đây thì
        # job nằm mãi trong pending/ và cron lặp lại đúng clip lỗi mỗi ngày.
        error = describe_error(exc)
        if dry_run:
            # Diễn tập: không đổi hàng chờ, không gửi cảnh báo.
            return False, error
        if from_queue:
            dest = queue_fail(json_path, error)
            log(f"lỗi, moved to {dest} (chi tiết ở _queue.last_error)")
        notify_publish_failure(json_path.stem, error)
        return False, error
    if from_queue and not dry_run:
        dest = queue_move(json_path, "done")
        log(f"moved to {dest}")
    return True, ""


def run_batch(args, targets: list[str], cfg) -> int:
    """Xử lý tối đa `--max` clip trong hàng chờ.

    Dừng sớm khi gặp 2 lỗi liên tiếp giống nhau (thường là lỗi hệ thống như token hỏng),
    để không đốt cả hàng chờ vào cùng một nguyên nhân.

    @param args tham số dòng lệnh (dùng --max)
    @param targets nền tảng cross-post
    @param cfg config đang dùng
    @returns exit code (0 nếu mọi clip thành công)
    """
    limit = int(args.max)
    dry_run = bool(getattr(args, "dry_run", False))
    if dry_run and limit == 0:
        # Diễn tập không di chuyển job khỏi pending/ nên vòng lặp sẽ lấy lại đúng clip đó.
        limit = 1
        log("dry-run: chỉ diễn tập 1 clip trong hàng chờ")
    done = failed = 0
    previous_error = ""
    attempts = 0
    while limit == 0 or attempts < limit:
        json_path = next_pending()
        if json_path is None:
            if attempts == 0:
                if not dry_run:
                    notify_empty_queue()
                raise SystemExit(
                    "Hàng chờ trống. python3 src/jobqueue.py add data/samples/clip_001.json"
                )
            break
        attempts += 1
        log(f"queue: {json_path} ({attempts}{'' if limit == 0 else f'/{limit}'})")
        ok, error = publish_clip(json_path, args, targets, from_queue=True)
        if ok:
            done += 1
            previous_error = ""
            continue
        failed += 1
        if error and error == previous_error:
            detail = f"lỗi lặp lại ({error}) — dừng batch sau {attempts} clip"
            log(detail)
            if not dry_run:
                notify(detail, level="error")
            break
        previous_error = error
    log(f"batch: {done} thành công, {failed} lỗi")
    return 1 if failed else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Render + upload Facebook Reels")
    parser.add_argument("--json", default="", help="File JSON cụ thể")
    parser.add_argument("--queue", action="store_true", help="Lấy clip pending đầu tiên")
    parser.add_argument("--footage", default="")
    parser.add_argument("--music", default="")
    parser.add_argument("--voice", default="")
    parser.add_argument("--state", default=os.getenv("FB_DEFAULT_STATE", "PUBLISHED"))
    parser.add_argument("--skip-upload", action="store_true")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Render thật rồi in kế hoạch request Facebook + cross-post, không gọi mạng "
             "(không cần token, không đổi hàng chờ)",
    )
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--no-first-comment", action="store_true", help="Không đăng comment đầu")
    parser.add_argument(
        "--max", type=int, default=1,
        help="Số clip lấy từ hàng chờ trong 1 lần chạy; 0 = dọn hết queue",
    )
    parser.add_argument(
        "--crosspost", default=None,
        help="Đăng thêm nền tảng khác: 'youtube,tiktok' hoặc 'none' (mặc định theo config)",
    )
    args = parser.parse_args()
    if args.max < 0:
        raise SystemExit("--max phải >= 0 (0 = dọn hết queue)")

    json_path: Path | None = Path(args.json) if args.json else None
    from_queue = bool(args.queue or json_path is None)
    cfg = load_config()
    stale = float(cfg.queue.get("stale_lock_seconds", DEFAULT_STALE_LOCK_SECONDS))
    targets = resolve_targets(args.crosspost, cfg)

    # Khoá trước khi đọc pending: hai cron chạy chồng sẽ lấy trùng 1 clip.
    with queue_lock(stale) if from_queue else nullcontext():
        if not from_queue:
            ok, error = publish_clip(json_path, args, targets, from_queue=False)
            if not ok:
                raise SystemExit(f"Đăng thất bại: {error}")
            return
        code = run_batch(args, targets, cfg)
        if code:
            # Batch đã ghi lỗi vào _queue.last_error và gửi cảnh báo; exit code để cron biết.
            raise SystemExit(code)


if __name__ == "__main__":
    main()
