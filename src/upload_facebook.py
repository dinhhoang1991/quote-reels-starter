#!/usr/bin/env python3
"""Upload a local MP4 to a Facebook Page as a Reel (official Graph API).

Chỉ đăng được lên Page. Không dùng được cho profile cá nhân / group.
Giới hạn Meta: tối đa 30 Reels API / Page / 24 giờ.

video_state: docs chính thức Reels API nêu PUBLISHED. DRAFT/SCHEDULED được gửi
nếu bạn chỉ định — Graph có thể từ chối; script in cảnh báo.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from checks import probe_duration
from config import load_config, root
from logutil import assert_can_publish, record_publish, remaining_quota
from schema import load_clip, validate_clip

try:
    import requests
except ImportError as exc:
    raise SystemExit("Chưa cài requests. Chạy: pip install requests") from exc

ROOT = root()
DEFAULT_VERSION = "v26.0"


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def api_error(resp: requests.Response) -> SystemExit:
    try:
        payload = resp.json()
    except Exception:
        payload = {"text": resp.text[:500]}
    return SystemExit(f"Facebook API {resp.status_code}: {json.dumps(payload, ensure_ascii=False)}")


def is_transient(resp: requests.Response) -> bool:
    if resp.status_code in {429, 500, 502, 503, 504}:
        return True
    try:
        err = (resp.json() or {}).get("error") or {}
    except Exception:
        return False
    return bool(err.get("is_transient"))


def request_with_retry(method: str, url: str, retries: int, **kwargs) -> requests.Response:
    delay = 1.5
    last: requests.Response | None = None
    for attempt in range(retries):
        resp = requests.request(method, url, **kwargs)
        last = resp
        if resp.status_code < 400:
            return resp
        if not is_transient(resp) or attempt == retries - 1:
            return resp
        print(f"  lỗi tạm {resp.status_code}, thử lại sau {delay:.1f}s ({attempt + 1}/{retries})")
        time.sleep(delay)
        delay *= 2
    assert last is not None
    return last


def start_session(page_id: str, token: str, version: str, retries: int) -> tuple[str, str]:
    url = f"https://graph.facebook.com/{version}/{page_id}/video_reels"
    resp = request_with_retry(
        "POST", url, retries, json={"upload_phase": "start", "access_token": token}, timeout=60
    )
    if resp.status_code >= 400:
        raise api_error(resp)
    data = resp.json()
    video_id = data.get("video_id")
    upload_url = data.get("upload_url")
    if not video_id or not upload_url:
        raise SystemExit(f"START không trả video_id/upload_url: {data}")
    return str(video_id), str(upload_url)


def upload_binary(upload_url: str, token: str, video_path: Path, retries: int) -> None:
    size = video_path.stat().st_size
    headers = {
        "Authorization": f"OAuth {token}",
        "offset": "0",
        "file_size": str(size),
        "Content-Type": "application/octet-stream",
    }
    delay = 1.5
    last_err = None
    for attempt in range(retries):
        with video_path.open("rb") as fh:
            resp = requests.post(upload_url, headers=headers, data=fh, timeout=600)
        if resp.status_code < 400:
            data = resp.json() if resp.content else {}
            if data and data.get("success") is False:
                raise SystemExit(f"Upload thất bại: {data}")
            return
        last_err = resp
        if not is_transient(resp) or attempt == retries - 1:
            raise api_error(resp)
        print(f"  upload tạm lỗi {resp.status_code}, thử lại ({attempt + 1}/{retries})")
        time.sleep(delay)
        delay *= 2
    if last_err is not None:
        raise api_error(last_err)


def wait_ready(video_id: str, token: str, version: str, timeout_s: int = 180) -> dict:
    url = f"https://graph.facebook.com/{version}/{video_id}"
    deadline = time.time() + timeout_s
    last: dict = {}
    while time.time() < deadline:
        resp = requests.get(url, params={"fields": "status", "access_token": token}, timeout=30)
        if resp.status_code >= 400:
            raise api_error(resp)
        last = resp.json()
        status = last.get("status") or {}
        uploading = (status.get("uploading_phase") or {}).get("status", "")
        processing = (status.get("processing_phase") or {}).get("status", "")
        video_status = status.get("video_status") or ""
        print(f"  status: video={video_status} upload={uploading} process={processing}")
        if str(video_status).upper() in {"ERROR", "EXPIRED"}:
            raise SystemExit(f"Video lỗi: {json.dumps(last, ensure_ascii=False)}")
        if str(processing).upper() in {"COMPLETE", "COMPLETED"} or str(video_status).upper() in {
            "READY",
            "ENCODED",
        }:
            return last
        if str(uploading).upper() in {"COMPLETE", "COMPLETED"} and not processing:
            time.sleep(8)
            return last
        time.sleep(5)
    print("Hết thời gian chờ encode, vẫn thử publish.")
    return last


def finish_publish(
    page_id: str,
    token: str,
    version: str,
    video_id: str,
    description: str,
    title: str,
    state: str,
    scheduled_ts: int | None,
    retries: int,
) -> dict:
    url = f"https://graph.facebook.com/{version}/{page_id}/video_reels"
    payload = {
        "access_token": token,
        "video_id": video_id,
        "upload_phase": "finish",
        "video_state": state,
        "description": description,
        "title": title,
    }
    if state == "SCHEDULED":
        if not scheduled_ts:
            raise SystemExit("SCHEDULED cần --at UNIX timestamp")
        payload["scheduled_publish_time"] = scheduled_ts
        print("Cảnh báo: docs Reels API chính thức không liệt kê SCHEDULED — Graph có thể từ chối.")
    if state == "DRAFT":
        print("Cảnh báo: docs Reels API chính thức nêu video_state=PUBLISHED. DRAFT có thể bị từ chối.")
    resp = request_with_retry("POST", url, retries, data=payload, timeout=60)
    if resp.status_code >= 400:
        raise api_error(resp)
    return resp.json()


def caption_from_json(data: dict, extra_tags: str) -> str:
    caption = (data.get("caption") or "").strip()
    if not caption:
        from schema import default_caption

        caption = default_caption(data)
    if extra_tags and extra_tags not in caption:
        caption = caption.rstrip() + "\n\n" + extra_tags
    return caption


def upload_reel(
    video_path: Path,
    page_id: str,
    token: str,
    description: str,
    title: str,
    state: str = "PUBLISHED",
    version: str = DEFAULT_VERSION,
    scheduled_ts: int | None = None,
    clip_id: str = "",
    force: bool = False,
) -> dict:
    cfg = load_config()
    retries = int(cfg.facebook.max_retries)
    duration = probe_duration(video_path)
    lo, hi = float(cfg.video.min_seconds), float(cfg.video.max_seconds)
    if duration < lo or duration > hi:
        raise SystemExit(f"Video {duration:.1f}s ngoài khoảng Reels {lo:.0f}–{hi:.0f}s")
    if clip_id:
        assert_can_publish(clip_id, force=force)
    print(f"Quota còn {remaining_quota()}/{cfg.facebook.daily_limit} trong 24h")
    print("1) START session")
    video_id, upload_url = start_session(page_id, token, version, retries)
    print(f"   video_id={video_id}")
    print("2) UPLOAD file")
    upload_binary(upload_url, token, video_path, retries)
    print("3) WAIT encode")
    wait_ready(video_id, token, version)
    print(f"4) FINISH state={state}")
    result = finish_publish(
        page_id, token, version, video_id, description, title, state, scheduled_ts, retries
    )
    result["video_id"] = video_id
    result["reel_url"] = f"https://www.facebook.com/reel/{video_id}"
    if clip_id:
        record_publish(clip_id, video_id, state, result["reel_url"], {"title": title})
    return result


def main() -> None:
    load_env(ROOT / ".env")
    cfg = load_config()
    parser = argparse.ArgumentParser(description="Đăng Reels lên Facebook Page")
    parser.add_argument("--video", required=True, help="Đường dẫn mp4")
    parser.add_argument("--json", default="", help="JSON list để lấy caption/title")
    parser.add_argument("--caption", default="")
    parser.add_argument("--title", default="")
    parser.add_argument("--state", default=os.getenv("FB_DEFAULT_STATE", "PUBLISHED"))
    parser.add_argument("--at", type=int, default=0, help="UNIX time nếu SCHEDULED")
    parser.add_argument("--force", action="store_true", help="Đăng lại clip đã có trong log")
    args = parser.parse_args()

    page_id = os.getenv("FB_PAGE_ID", "").strip()
    token = os.getenv("FB_PAGE_ACCESS_TOKEN", "").strip()
    version = os.getenv("FB_API_VERSION", cfg.facebook.api_version).strip() or DEFAULT_VERSION
    tags = os.getenv("DEFAULT_HASHTAGS", "").strip()
    if not page_id or not token:
        raise SystemExit("Thiếu FB_PAGE_ID hoặc FB_PAGE_ACCESS_TOKEN. Copy .env.example thành .env")

    video_path = Path(args.video)
    if not video_path.exists():
        raise SystemExit(f"Không thấy video: {video_path}")

    data: dict = {}
    clip_id = ""
    if args.json:
        data = load_clip(Path(args.json))
        clip_id = data["id"]

    title = args.title or " ".join(data.get("title", "").split()) or video_path.stem
    if args.caption:
        caption = args.caption
    elif data:
        caption = caption_from_json(data, tags)
    else:
        caption = title

    result = upload_reel(
        video_path,
        page_id,
        token,
        caption,
        title,
        state=args.state.upper(),
        version=version,
        scheduled_ts=args.at or None,
        clip_id=clip_id,
        force=args.force,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
