#!/usr/bin/env python3
"""Đăng video lên TikTok (Content Posting API) theo luồng FILE_UPLOAD.

    python3 src/upload_tiktok.py --video assets/out/clip_001.mp4 --json data/samples/clip_001.json
    python3 src/upload_tiktok.py --video ... --json ... --dry-run

Cần trong .env:
    TIKTOK_ACCESS_TOKEN=...        # scope video.publish (và video.upload nếu chỉ đưa vào nháp)
    TIKTOK_PRIVACY_LEVEL=SELF_ONLY # app chưa được TikTok duyệt chỉ được SELF_ONLY

Lưu ý: máy này chưa từng gọi TikTok thật (cần app được duyệt), nên code bám theo protocol
FILE_UPLOAD của Content Posting API và được kiểm bằng HTTP giả trong tests/test_crosspost.py.
App chưa audit chỉ đăng được ở chế độ riêng tư — đó là lý do mặc định là SELF_ONLY.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from config import load_config, root
from logutil import log
from schema import load_clip
from upload_facebook import api_error, request_with_retry

ROOT = root()
INIT_URL = "https://open.tiktokapis.com/v2/post/publish/video/init/"
STATUS_URL = "https://open.tiktokapis.com/v2/post/publish/status/fetch/"
TITLE_LIMIT = 2200
DEFAULT_PRIVACY = "SELF_ONLY"
PRIVACY_LEVELS = ("PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS", "FOLLOWER_OF_CREATOR", "SELF_ONLY")


def load_tiktok_env(path: Path | None = None) -> dict[str, str]:
    """Đọc token TikTok từ môi trường/.env.

    @param path file .env, mặc định .env ở gốc repo
    @returns {"access_token", "privacy_level"}
    """
    from upload_facebook import load_env

    load_env(path or (ROOT / ".env"))
    privacy = os.getenv("TIKTOK_PRIVACY_LEVEL", DEFAULT_PRIVACY).strip().upper() or DEFAULT_PRIVACY
    return {
        "access_token": os.getenv("TIKTOK_ACCESS_TOKEN", "").strip(),
        "privacy_level": privacy,
    }


def tiktok_title(text: str, limit: int = TITLE_LIMIT) -> str:
    """Tiêu đề/caption TikTok: gộp khoảng trắng, cắt theo giới hạn.

    @param text caption gốc
    @param limit số ký tự tối đa
    @returns chuỗi hợp lệ
    """
    return " ".join(str(text).split())[:limit]


def init_payload(
    video_size: int,
    title: str,
    privacy_level: str = DEFAULT_PRIVACY,
    cover_timestamp_ms: int = 0,
    chunk_size: int | None = None,
) -> dict:
    """Body cho /post/publish/video/init/.

    @param video_size số byte của file
    @param title caption
    @param privacy_level một trong PRIVACY_LEVELS
    @param cover_timestamp_ms mốc lấy ảnh cover (ms), 0 là để TikTok tự chọn
    @param chunk_size kích thước mỗi chunk, mặc định bằng cả file (1 chunk)
    @returns dict body
    """
    if privacy_level not in PRIVACY_LEVELS:
        raise SystemExit(
            f"TIKTOK_PRIVACY_LEVEL không hợp lệ: {privacy_level} (chọn {' | '.join(PRIVACY_LEVELS)})"
        )
    size = max(int(video_size), 1)
    chunk = int(chunk_size) if chunk_size else size
    chunk = max(min(chunk, size), 1)
    total_chunks = (size + chunk - 1) // chunk
    post_info: dict = {
        "title": tiktok_title(title),
        "privacy_level": privacy_level,
        "disable_duet": False,
        "disable_comment": False,
        "disable_stitch": False,
    }
    if cover_timestamp_ms > 0:
        post_info["video_cover_timestamp_ms"] = int(cover_timestamp_ms)
    return {
        "post_info": post_info,
        "source_info": {
            "source": "FILE_UPLOAD",
            "video_size": size,
            "chunk_size": chunk,
            "total_chunk_count": total_chunks,
        },
    }


def init_upload(payload: dict, token: str, retries: int = 3) -> tuple[str, str]:
    """Mở phiên upload, trả về (publish_id, upload_url).

    @param payload body từ init_payload
    @param token access token
    @param retries số lần thử khi lỗi tạm
    @returns (publish_id, upload_url)
    """
    resp = request_with_retry(
        "POST", INIT_URL, retries,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=UTF-8",
        },
        data=json.dumps(payload).encode("utf-8"),
        timeout=60,
    )
    if resp.status_code >= 400:
        raise api_error(resp)
    body = resp.json() if resp.content else {}
    error = body.get("error") or {}
    if str(error.get("code") or "ok") not in {"ok", ""}:
        raise SystemExit(f"TikTok từ chối init: {json.dumps(error, ensure_ascii=False)}")
    data = body.get("data") or {}
    publish_id = str(data.get("publish_id") or "")
    upload_url = str(data.get("upload_url") or "")
    if not publish_id or not upload_url:
        raise SystemExit(f"TikTok init thiếu publish_id/upload_url: {json.dumps(body, ensure_ascii=False)}")
    return publish_id, upload_url


def upload_chunk(upload_url: str, video: Path, offset: int, size: int, total: int,
                 retries: int = 3) -> None:
    """Đẩy một chunk lên upload_url.

    @param upload_url URL TikTok trả về
    @param video file mp4
    @param offset byte bắt đầu
    @param size số byte của chunk
    @param total tổng số byte của file
    @param retries số lần thử khi lỗi tạm
    """
    with video.open("rb") as handle:
        handle.seek(offset)
        chunk = handle.read(size)
    resp = request_with_retry(
        "PUT", upload_url, retries,
        headers={
            "Content-Type": "video/mp4",
            "Content-Length": str(len(chunk)),
            "Content-Range": f"bytes {offset}-{offset + len(chunk) - 1}/{total}",
        },
        data=chunk,
        timeout=900,
    )
    if resp.status_code >= 400:
        raise api_error(resp)


def publish_status(publish_id: str, token: str, retries: int = 3) -> dict:
    """Hỏi trạng thái xử lý của một publish_id.

    @param publish_id id TikTok trả về
    @param token access token
    @param retries số lần thử khi lỗi tạm
    @returns data trạng thái
    """
    resp = request_with_retry(
        "POST", STATUS_URL, retries,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=UTF-8",
        },
        data=json.dumps({"publish_id": publish_id}).encode("utf-8"),
        timeout=60,
    )
    if resp.status_code >= 400:
        raise api_error(resp)
    return (resp.json() or {}).get("data") or {}


def upload_clip(
    video: Path,
    title: str = "",
    privacy_level: str | None = None,
    cover_timestamp_ms: int = 0,
    dry_run: bool = False,
) -> dict:
    """Đăng video lên TikTok.

    @param video file mp4 đã render
    @param title caption
    @param privacy_level ghi đè TIKTOK_PRIVACY_LEVEL
    @param cover_timestamp_ms mốc ảnh cover (ms)
    @param dry_run chỉ in request, không gọi mạng
    @returns dict kết quả
    """
    cfg = load_config()
    env = load_tiktok_env()
    privacy = (privacy_level or env["privacy_level"]).upper()
    size = video.stat().st_size if video.exists() else 0
    payload = init_payload(size, title, privacy, cover_timestamp_ms)
    if dry_run:
        return {
            "target": "tiktok",
            "dry_run": True,
            "url": INIT_URL,
            "payload": payload,
            "video_bytes": size,
        }
    token = env["access_token"]
    if not token:
        raise SystemExit("Thiếu TIKTOK_ACCESS_TOKEN trong .env")
    retries = int(cfg.facebook.max_retries)
    publish_id, upload_url = init_upload(payload, token, retries)
    source = payload["source_info"]
    log(f"TikTok: publish_id={publish_id}, chunk {source['chunk_size']}B "
        f"x{source['total_chunk_count']}")
    offset = 0
    while offset < size:
        upload_chunk(upload_url, video, offset, source["chunk_size"], size, retries)
        offset += source["chunk_size"]
    result = {"target": "tiktok", "publish_id": publish_id, "privacy_level": privacy}
    try:
        result["status"] = publish_status(publish_id, token, retries)
    except (SystemExit, Exception) as exc:
        result["status_error"] = str(exc)
        log(f"cảnh báo: không đọc được trạng thái TikTok ({exc})")
    return result


def main() -> None:
    cfg = load_config()
    parser = argparse.ArgumentParser(description="Đăng video lên TikTok")
    parser.add_argument("--video", required=True)
    parser.add_argument("--json", default="")
    parser.add_argument("--title", default="")
    parser.add_argument("--privacy", default=str((cfg.get("crosspost", {}) or {}).get(
        "tiktok_privacy", "")))
    parser.add_argument("--cover-ms", type=int, default=int((cfg.get("cover", {}) or {}).get(
        "tiktok_cover_ms", 0) or 0))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    video = Path(args.video)
    if not video.exists():
        raise SystemExit(f"Không thấy video: {video}")
    data = load_clip(Path(args.json)) if args.json else {}
    title = args.title or str(data.get("caption") or data.get("title") or video.stem)
    result = upload_clip(
        video, title, privacy_level=args.privacy or None,
        cover_timestamp_ms=args.cover_ms, dry_run=args.dry_run,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
