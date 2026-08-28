#!/usr/bin/env python3
"""Upload a local MP4 to a Facebook Page as a Reel (official Graph API).

Chỉ đăng được lên Page. Không dùng được cho profile cá nhân / group.
Giới hạn Meta: tối đa 30 Reels API / Page / 24 giờ.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

try:
    import requests
except ImportError as exc:
    raise SystemExit("Chưa cài requests. Chạy: pip install requests") from exc

ROOT = Path(__file__).resolve().parents[1]
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


def start_session(page_id: str, token: str, version: str) -> tuple[str, str]:
    url = f"https://graph.facebook.com/{version}/{page_id}/video_reels"
    resp = requests.post(url, json={"upload_phase": "start", "access_token": token}, timeout=60)
    if resp.status_code >= 400:
        raise api_error(resp)
    data = resp.json()
    video_id = data.get("video_id")
    upload_url = data.get("upload_url")
    if not video_id or not upload_url:
        raise SystemExit(f"START không trả video_id/upload_url: {data}")
    return str(video_id), str(upload_url)


def upload_binary(upload_url: str, token: str, video_path: Path) -> None:
    size = video_path.stat().st_size
    headers = {
        "Authorization": f"OAuth {token}",
        "offset": "0",
        "file_size": str(size),
        "Content-Type": "application/octet-stream",
    }
    with video_path.open("rb") as fh:
        resp = requests.post(upload_url, headers=headers, data=fh, timeout=600)
    if resp.status_code >= 400:
        raise api_error(resp)
    data = resp.json() if resp.content else {}
    if data and data.get("success") is False:
        raise SystemExit(f"Upload thất bại: {data}")


def wait_ready(video_id: str, token: str, version: str, timeout_s: int = 180) -> dict:
    url = f"https://graph.facebook.com/{version}/{video_id}"
    deadline = time.time() + timeout_s
    last = {}
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
        # Một số account không có processing_phase rõ; nếu upload xong thì cho qua
        if str(uploading).upper() in {"COMPLETE", "COMPLETED"} and not processing:
            time.sleep(8)
            return last
        time.sleep(5)
    print("Hết thời gian chờ encode, vẫn thử publish.")
    return last


def publish(
    page_id: str,
    token: str,
    version: str,
    video_id: str,
    description: str,
    title: str,
    state: str,
    scheduled_ts: int | None = None,
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
    resp = requests.post(url, data=payload, timeout=60)
    if resp.status_code >= 400:
        raise api_error(resp)
    return resp.json()


def caption_from_json(data: dict, extra_tags: str) -> str:
    if data.get("caption"):
        caption = data["caption"].strip()
    else:
        title = " ".join(data.get("title", "").split())
        footer = data.get("footer", "")
        lines = [title]
        for idx, item in enumerate(data.get("items", []), start=1):
            lines.append(f"{idx}. {item.get('label', '')}: {item.get('text', '')}")
        if footer:
            lines.append("")
            lines.append(footer)
        caption = "\n".join(lines)
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
) -> dict:
    print("1) START session")
    video_id, upload_url = start_session(page_id, token, version)
    print(f"   video_id={video_id}")
    print("2) UPLOAD file")
    upload_binary(upload_url, token, video_path)
    print("3) WAIT encode")
    wait_ready(video_id, token, version)
    print(f"4) FINISH state={state}")
    result = publish(page_id, token, version, video_id, description, title, state, scheduled_ts)
    result["video_id"] = video_id
    result["reel_url"] = f"https://www.facebook.com/reel/{video_id}"
    return result


def main() -> None:
    load_env(ROOT / ".env")
    parser = argparse.ArgumentParser(description="Đăng Reels lên Facebook Page")
    parser.add_argument("--video", required=True, help="Đường dẫn mp4")
    parser.add_argument("--json", default="", help="JSON list để lấy caption/title")
    parser.add_argument("--caption", default="")
    parser.add_argument("--title", default="")
    parser.add_argument("--state", default=os.getenv("FB_DEFAULT_STATE", "PUBLISHED"))
    parser.add_argument("--at", type=int, default=0, help="UNIX time nếu SCHEDULED")
    args = parser.parse_args()

    page_id = os.getenv("FB_PAGE_ID", "").strip()
    token = os.getenv("FB_PAGE_ACCESS_TOKEN", "").strip()
    version = os.getenv("FB_API_VERSION", DEFAULT_VERSION).strip() or DEFAULT_VERSION
    tags = os.getenv("DEFAULT_HASHTAGS", "").strip()
    if not page_id or not token:
        raise SystemExit("Thiếu FB_PAGE_ID hoặc FB_PAGE_ACCESS_TOKEN. Copy .env.example thành .env")

    video_path = Path(args.video)
    if not video_path.exists():
        raise SystemExit(f"Không thấy video: {video_path}")

    data = {}
    if args.json:
        data = json.loads(Path(args.json).read_text(encoding="utf-8"))

    title = args.title or " ".join(data.get("title", "").split()) or video_path.stem
    caption = args.caption or caption_from_json(data, tags) if data else (args.caption or title)

    result = upload_reel(
        video_path,
        page_id,
        token,
        caption,
        title,
        state=args.state.upper(),
        version=version,
        scheduled_ts=args.at or None,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
