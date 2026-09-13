#!/usr/bin/env python3
"""Đăng Shorts lên YouTube (Data API v3) bằng OAuth refresh token.

    python3 src/upload_youtube.py --video assets/out/clip_001.mp4 --json data/samples/clip_001.json
    python3 src/upload_youtube.py --video ... --json ... --dry-run     # in request, không gọi mạng
    python3 src/upload_youtube.py --help-auth                          # cần gì trong .env

Cần trong .env (tạo OAuth client "Desktop app" trong Google Cloud Console):
    YOUTUBE_CLIENT_ID=...
    YOUTUBE_CLIENT_SECRET=...
    YOUTUBE_REFRESH_TOKEN=...      # scope https://www.googleapis.com/auth/youtube.upload

Lưu ý: máy này chưa từng gọi YouTube thật (không có app được duyệt), nên code được viết theo
đúng protocol resumable upload của Google và kiểm bằng HTTP giả trong tests/test_crosspost.py.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from config import load_config, root
from logutil import already_crossposted, crosspost_state, log, record_publish
from schema import compose_caption, load_clip
from upload_facebook import api_error, request_file_with_retry, request_with_retry

ROOT = root()
TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
THUMB_URL = "https://www.googleapis.com/upload/youtube/v3/thumbnails/set"
DEFAULT_CATEGORY = "22"  # People & Blogs
TITLE_LIMIT = 100
DESCRIPTION_LIMIT = 5000
SHORTS_TAG = "#Shorts"


def load_youtube_env(path: Path | None = None) -> dict[str, str]:
    """Đọc credential YouTube từ môi trường/.env.

    @param path file .env, mặc định .env ở gốc repo
    @returns {"client_id", "client_secret", "refresh_token"} (rỗng nếu thiếu)
    """
    from upload_facebook import load_env

    load_env(path or (ROOT / ".env"))
    return {
        "client_id": os.getenv("YOUTUBE_CLIENT_ID", "").strip(),
        "client_secret": os.getenv("YOUTUBE_CLIENT_SECRET", "").strip(),
        "refresh_token": os.getenv("YOUTUBE_REFRESH_TOKEN", "").strip(),
    }


def shorts_title(title: str, limit: int = TITLE_LIMIT) -> str:
    """Tiêu đề Shorts: bỏ xuống dòng, thêm #Shorts nếu thiếu, cắt theo giới hạn YouTube.

    @param title tiêu đề gốc
    @param limit số ký tự tối đa
    @returns tiêu đề hợp lệ
    """
    flat = " ".join(str(title).split())
    if SHORTS_TAG.lower() not in flat.lower():
        flat = f"{flat} {SHORTS_TAG}".strip()
    return flat[:limit]


def shorts_description(caption: str, tags: str = "") -> str:
    """Mô tả Shorts: caption + hashtag, cắt theo giới hạn YouTube.

    @param caption caption Facebook/JSON
    @param tags hashtag thêm vào cuối
    @returns mô tả hợp lệ
    """
    text = str(caption).rstrip()
    if tags and tags not in text:
        text = f"{text}\n\n{tags}".strip()
    return text[:DESCRIPTION_LIMIT]


def video_metadata(
    title: str, description: str, tags: list[str], category_id: str = DEFAULT_CATEGORY,
    privacy_status: str = "public",
) -> dict:
    """Body cho videos.insert (part=snippet,status).

    @param title tiêu đề (đã qua shorts_title)
    @param description mô tả
    @param tags danh sách tag không có dấu #
    @param category_id categoryId của YouTube
    @param privacy_status public | unlisted | private
    @returns dict metadata
    """
    return {
        "snippet": {
            "title": title,
            "description": description,
            "tags": [tag.lstrip("#") for tag in tags if tag.strip()],
            "categoryId": str(category_id),
        },
        "status": {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": False,
        },
    }


def access_token(client_id: str, client_secret: str, refresh_token: str, retries: int = 3) -> str:
    """Đổi refresh token lấy access token mới.

    @param client_id OAuth client id
    @param client_secret OAuth client secret
    @param refresh_token refresh token đã lấy khi cấp quyền
    @param retries số lần thử khi lỗi tạm
    @returns access token
    """
    if not (client_id and client_secret and refresh_token):
        raise SystemExit("Thiếu YOUTUBE_CLIENT_ID / YOUTUBE_CLIENT_SECRET / YOUTUBE_REFRESH_TOKEN")
    resp = request_with_retry(
        "POST", TOKEN_URL, retries,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=30,
    )
    if resp.status_code >= 400:
        raise SystemExit(f"YouTube token lỗi {resp.status_code}: {resp.text[:300]}")
    token = str(resp.json().get("access_token") or "")
    if not token:
        raise SystemExit(f"YouTube không trả access_token: {resp.text[:300]}")
    return token


def init_resumable_upload(
    video: Path, token: str, metadata: dict, retries: int = 3
) -> str:
    """Mở phiên resumable upload, trả về URL phiên.

    @param video file mp4
    @param token access token
    @param metadata body videos.insert
    @param retries số lần thử khi lỗi tạm
    @returns URL upload của phiên
    """
    size = video.stat().st_size
    resp = request_with_retry(
        "POST", UPLOAD_URL, retries,
        params={"uploadType": "resumable", "part": "snippet,status"},
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Type": "video/mp4",
            "X-Upload-Content-Length": str(size),
        },
        data=json.dumps(metadata).encode("utf-8"),
        timeout=60,
    )
    if resp.status_code >= 400:
        raise SystemExit(f"YouTube init lỗi {resp.status_code}: {resp.text[:300]}")
    location = resp.headers.get("Location") or resp.headers.get("location")
    if not location:
        raise SystemExit("YouTube không trả header Location cho phiên upload")
    return str(location)


def upload_bytes(session_url: str, video: Path, token: str, retries: int = 3) -> dict:
    """Đẩy toàn bộ file lên URL phiên (một request, đủ cho Shorts < 64MB).

    @param session_url URL từ init_resumable_upload
    @param video file mp4
    @param token access token
    @param retries số lần thử khi lỗi tạm
    @returns video resource của YouTube
    """
    size = video.stat().st_size
    resp = request_file_with_retry(
        "PUT", session_url, video, retries,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "video/mp4",
            "Content-Length": str(size),
        },
        timeout=900,
    )
    if resp.status_code == 308:
        raise SystemExit("YouTube nhận một phần file (308) — file quá lớn cho upload 1 request")
    if resp.status_code >= 400:
        raise api_error(resp)
    return resp.json()


def set_thumbnail(video_id: str, token: str, image: Path, retries: int = 3) -> dict:
    """Đặt ảnh cover cho video (JPEG/PNG ≤ 2MB).

    @param video_id id video YouTube
    @param token access token
    @param image ảnh cover
    @param retries số lần thử khi lỗi tạm
    @returns response của YouTube
    """
    with image.open("rb") as handle:
        resp = request_with_retry(
            "POST", THUMB_URL, retries,
            params={"videoId": video_id},
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "image/jpeg",
                "Content-Length": str(image.stat().st_size),
            },
            data=handle,
            timeout=300,
        )
    if resp.status_code >= 400:
        raise api_error(resp)
    return resp.json()


def parse_tags(raw: str) -> list[str]:
    """Tách chuỗi hashtag thành danh sách tag không dấu #."""
    return [part.lstrip("#") for part in str(raw).replace(",", " ").split() if part.strip("#")]


def upload_clip(
    video: Path,
    data: dict | None = None,
    caption: str = "",
    tags: str = "",
    cover: Path | None = None,
    privacy: str = "public",
    dry_run: bool = False,
    clip_id: str = "",
    force: bool = False,
) -> dict:
    """Đăng clip lên YouTube dưới dạng Shorts.

    @param video file mp4 đã render
    @param data clip đã validate (lấy title/caption)
    @param caption caption dùng làm mô tả
    @param tags hashtag
    @param cover ảnh cover, None thì bỏ qua
    @param privacy public | unlisted | private
    @param dry_run chỉ in request, không gọi mạng
    @param clip_id id clip để chống đăng trùng, rỗng thì bỏ qua log
    @param force đăng lại dù đã có trong log
    @returns dict kết quả (có video_id/url, hoặc request khi dry_run)
    """
    cfg = load_config()
    data = data or {}
    section = cfg.get("crosspost", {}) or {}
    platform_tags = str(section.get("youtube_tags", "") or "")
    title = shorts_title(str(data.get("title") or video.stem))
    # Ưu tiên `youtube_caption` trong JSON, rồi caption chung; hashtag chung + hashtag YouTube.
    base_caption = str(data.get("youtube_caption") or caption or data.get("caption") or "")
    description = shorts_description(
        compose_caption(base_caption, platform_tags), compose_caption(tags, platform_tags)
    )
    metadata = video_metadata(
        title, description, parse_tags(compose_caption(tags, platform_tags)).copy(),
        category_id=str(section.get("youtube_category_id", DEFAULT_CATEGORY)),
        privacy_status=privacy,
    )
    if dry_run:
        return {
            "target": "youtube",
            "dry_run": True,
            "url": UPLOAD_URL,
            "params": {"uploadType": "resumable", "part": "snippet,status"},
            "metadata": metadata,
            "video_bytes": video.stat().st_size if video.exists() else 0,
            "cover": str(cover) if cover else "",
        }
    if clip_id and not force:
        previous = already_crossposted(clip_id, "youtube")
        if previous:
            log(f"YouTube: {clip_id} đã đăng lúc {previous.get('iso')} — bỏ qua "
                f"(dùng --force nếu muốn đăng lại)")
            return {
                "target": "youtube",
                "skipped": True,
                "reason": "đã cross-post trước đó",
                "url": str(previous.get("url", "")),
            }
    env = load_youtube_env()
    retries = int(cfg.facebook.max_retries)
    token = access_token(env["client_id"], env["client_secret"], env["refresh_token"], retries)
    session = init_resumable_upload(video, token, metadata, retries)
    log(f"YouTube: đang đẩy {video.name} ({video.stat().st_size / 1024 / 1024:.1f} MB)")
    resource = upload_bytes(session, video, token, retries)
    video_id = str(resource.get("id") or "")
    result = {
        "target": "youtube",
        "video_id": video_id,
        "url": f"https://youtube.com/shorts/{video_id}" if video_id else "",
        "title": title,
    }
    if cover and cover.exists():
        try:
            set_thumbnail(video_id, token, cover, retries)
            result["thumbnail"] = str(cover)
        except (SystemExit, Exception) as exc:
            result["thumbnail_error"] = str(exc)
            log(f"cảnh báo: không đặt được thumbnail YouTube ({exc})")
    if clip_id:
        record_publish(
            clip_id, video_id, crosspost_state("youtube"), str(result["url"]), {"target": "youtube"}
        )
    return result


def main() -> None:
    cfg = load_config()
    parser = argparse.ArgumentParser(description="Đăng Shorts lên YouTube")
    parser.add_argument("--video", required=True)
    parser.add_argument("--json", default="")
    parser.add_argument("--caption", default="")
    parser.add_argument("--tags", default=os.getenv("DEFAULT_HASHTAGS", ""))
    parser.add_argument("--cover", default="")
    parser.add_argument("--privacy", default=str((cfg.get("crosspost", {}) or {}).get(
        "youtube_privacy", "public")))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="Đăng lại dù đã có trong log")
    parser.add_argument("--help-auth", action="store_true", help="In biến môi trường cần có")
    args = parser.parse_args()

    if args.help_auth:
        missing = [key for key, value in load_youtube_env().items() if not value]
        print("Cần trong .env: YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET, YOUTUBE_REFRESH_TOKEN")
        print("Thiếu:", ", ".join(missing) or "không thiếu")
        return

    video = Path(args.video)
    if not video.exists():
        raise SystemExit(f"Không thấy video: {video}")
    data = load_clip(Path(args.json)) if args.json else {}
    result = upload_clip(
        video, data, caption=args.caption, tags=args.tags,
        cover=Path(args.cover) if args.cover else None,
        privacy=args.privacy, dry_run=args.dry_run,
        clip_id=str(data.get("id", "")), force=args.force,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
