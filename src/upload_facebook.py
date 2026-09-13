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
from logutil import assert_can_publish, log, record_publish, remaining_quota
from schema import load_clip

try:
    import requests
except ImportError as exc:
    raise SystemExit("Chưa cài requests. Chạy: pip install requests") from exc

ROOT = root()
DEFAULT_VERSION = "v26.0"
DEFAULT_MAX_DELAY = 900.0

# Lỗi Graph nên thử lại: lỗi tạm phía Meta + hạn mức (app/user/page/custom).
# Meta trả các mã hạn mức này kèm HTTP 400 nên không thể chỉ nhìn status code.
RETRYABLE_ERROR_CODES = {1, 2, 4, 17, 32, 341, 613}
RATE_LIMIT_ERROR_CODES = {4, 17, 32, 613}
RATE_LIMIT_HINT = " (hạn mức Graph — chờ rồi chạy lại, hoặc giảm số Reels/ngày)"


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def error_payload(resp: requests.Response) -> dict:
    """Object `error` trong body Graph; {} nếu body không phải JSON lỗi.

    @param resp response từ Graph API
    @returns error object, rỗng khi không đọc được
    """
    try:
        payload = resp.json()
    except ValueError:
        return {}
    if not isinstance(payload, dict):
        return {}
    error = payload.get("error")
    return error if isinstance(error, dict) else {}


def api_error(resp: requests.Response) -> SystemExit:
    """SystemExit kèm error object đầy đủ và gợi ý khi lỗi là hạn mức."""
    try:
        payload = resp.json()
    except ValueError:
        payload = {"text": resp.text[:500]}
    error = error_payload(resp)
    hint = RATE_LIMIT_HINT if error.get("code") in RATE_LIMIT_ERROR_CODES else ""
    return SystemExit(
        f"Facebook API {resp.status_code}: {json.dumps(payload, ensure_ascii=False)}{hint}"
    )


def is_transient(resp: requests.Response) -> bool:
    """Lỗi có nên thử lại: 429/5xx/408, `is_transient` của Meta, hoặc mã hạn mức."""
    if resp.status_code in {408, 429, 500, 502, 503, 504}:
        return True
    error = error_payload(resp)
    if bool(error.get("is_transient")):
        return True
    return error.get("code") in RETRYABLE_ERROR_CODES


def retry_delay(resp: requests.Response, attempt: int, max_delay: float) -> float:
    """Chờ bao lâu trước lần thử lại.

    Ưu tiên `estimated_time_to_regain_access` (phút) mà Graph trả cho lỗi hạn mức,
    nếu không có thì backoff luỹ thừa 1.5s * 2^attempt, luôn kẹp bởi `max_delay`.

    @param resp response lỗi
    @param attempt số lần đã thử (0-based)
    @param max_delay trần thời gian chờ (giây)
    @returns số giây cần chờ
    """
    error = error_payload(resp)
    data = error.get("error_data")
    estimated = data.get("estimated_time_to_regain_access") if isinstance(data, dict) else None
    if estimated is not None:
        try:
            return min(float(estimated) * 60.0, max_delay)
        except (TypeError, ValueError):
            pass
    return min(1.5 * (2 ** attempt), max_delay)


def usage_headers(resp: requests.Response) -> list[str]:
    """Các dòng mô tả mức dùng hạn mức từ header của Graph.

    @param resp response bất kỳ từ Graph
    @returns danh sách dòng đã format, rỗng nếu Meta không gửi header
    """
    lines: list[str] = []
    for header in ("X-App-Usage", "X-Business-Use-Case-Usage"):
        raw = resp.headers.get(header)
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
        except ValueError:
            continue
        for scope, usage in _usage_entries(parsed):
            lines.append(
                f"usage {header} {scope}: call={usage.get('call_count', '?')}% "
                f"cpu={usage.get('total_cputime', '?')}% time={usage.get('total_time', '?')}%"
            )
    return lines


def _usage_entries(parsed: object) -> list[tuple[str, dict]]:
    """Chuẩn hoá 2 dạng header: phẳng (X-App-Usage) và lồng theo business id."""
    if not isinstance(parsed, dict):
        return []
    if "call_count" in parsed:
        return [("app", parsed)]
    return [
        (str(scope), usage)
        for scope, usage in parsed.items()
        if isinstance(usage, dict)
    ]


def request_with_retry(method: str, url: str, retries: int, **kwargs) -> requests.Response:
    cfg = load_config()
    max_delay = float(cfg.facebook.get("retry_max_delay_seconds", DEFAULT_MAX_DELAY))
    last: requests.Response | None = None
    for attempt in range(retries):
        resp = requests.request(method, url, **kwargs)
        last = resp
        for line in usage_headers(resp):
            log(line)
        if resp.status_code < 400:
            return resp
        if not is_transient(resp) or attempt == retries - 1:
            return resp
        delay = retry_delay(resp, attempt, max_delay)
        log(f"lỗi tạm {resp.status_code} (code={error_payload(resp).get('code')}), "
            f"thử lại sau {delay:.1f}s ({attempt + 1}/{retries})")
        time.sleep(delay)
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


def request_file_with_retry(
    method: str, url: str, file_path: Path, retries: int, headers: dict, timeout: int
) -> requests.Response:
    """Gửi file lên URL, mở lại file ở MỖI lần thử.

    Mở file một lần ngoài vòng lặp là lỗi: lần thử lại sẽ đọc từ cuối file và gửi body rỗng.

    @param method HTTP method (POST/PUT)
    @param url đích upload
    @param file_path file cần gửi
    @param retries số lần thử tối đa
    @param headers header cố định, gồm cả Content-Length nếu server cần
    @param timeout timeout mỗi request (giây)
    @returns response cuối cùng (đã thử lại các lỗi tạm)
    """
    cfg = load_config()
    max_delay = float(cfg.facebook.get("retry_max_delay_seconds", DEFAULT_MAX_DELAY))
    last: requests.Response | None = None
    for attempt in range(retries):
        with file_path.open("rb") as handle:
            resp = requests.request(method, url, headers=headers, data=handle, timeout=timeout)
        last = resp
        for line in usage_headers(resp):
            log(line)
        if resp.status_code < 400:
            return resp
        if not is_transient(resp) or attempt == retries - 1:
            return resp
        delay = retry_delay(resp, attempt, max_delay)
        log(f"upload tạm lỗi {resp.status_code}, thử lại sau {delay:.1f}s ({attempt + 1}/{retries})")
        time.sleep(delay)
    assert last is not None
    return last


def upload_binary(upload_url: str, token: str, video_path: Path, retries: int) -> None:
    size = video_path.stat().st_size
    headers = {
        "Authorization": f"OAuth {token}",
        "offset": "0",
        "file_size": str(size),
        "Content-Type": "application/octet-stream",
    }
    resp = request_file_with_retry("POST", upload_url, video_path, retries, headers, 600)
    if resp.status_code >= 400:
        raise api_error(resp)
    data = resp.json() if resp.content else {}
    if data and data.get("success") is False:
        raise SystemExit(f"Upload thất bại: {data}")


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
        log(f"status: video={video_status} upload={uploading} process={processing}")
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
    log("hết thời gian chờ encode, vẫn thử publish.")
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
    thumb_offset_ms: int = 0,
) -> dict:
    """Chốt phiên upload Reel.

    @param thumb_offset_ms mốc (ms) lấy làm ảnh cover; 0 là để Meta tự chọn
    @returns response của Graph
    """
    url = f"https://graph.facebook.com/{version}/{page_id}/video_reels"
    payload = {
        "access_token": token,
        "video_id": video_id,
        "upload_phase": "finish",
        "video_state": state,
        "description": description,
        "title": title,
    }
    if thumb_offset_ms > 0:
        payload["thumb_offset"] = int(thumb_offset_ms)
    if state == "SCHEDULED":
        if not scheduled_ts:
            raise SystemExit("SCHEDULED cần --at UNIX timestamp")
        payload["scheduled_publish_time"] = scheduled_ts
        log("cảnh báo: docs Reels API chính thức không liệt kê SCHEDULED — Graph có thể từ chối.")
    if state == "DRAFT":
        log("cảnh báo: docs Reels API chính thức nêu video_state=PUBLISHED. DRAFT có thể bị từ chối.")
    resp = request_with_retry("POST", url, retries, data=payload, timeout=60)
    if resp.status_code >= 400:
        raise api_error(resp)
    return resp.json()


class _SafeValues(dict):
    """Thiếu placeholder thì thay bằng chuỗi rỗng thay vì ném KeyError."""

    def __missing__(self, key: str) -> str:
        return ""


def render_template(template: str, values: dict[str, str]) -> str:
    """Điền placeholder `{ten}` trong template, thiếu giá trị thì bỏ trống.

    @param template chuỗi có placeholder
    @param values giá trị thay thế
    @returns chuỗi đã điền, khoảng trắng thừa đã gộp
    """
    return " ".join(str(template).format_map(_SafeValues(values)).split())


def first_comment_text(clip: dict | None, cfg=None) -> str:
    """Nội dung comment đầu tiên cho Reel.

    Ưu tiên `first_comment` trong clip, sau đó tới template `facebook.first_comment`
    (placeholder: title, footer, topic, caption). Rỗng = không đăng comment.

    @param clip clip đã validate, có thể None
    @param cfg config, mặc định đọc config.yaml
    @returns nội dung comment, rỗng nếu tắt
    """
    cfg = cfg or load_config()
    direct = str((clip or {}).get("first_comment") or "").strip()
    if direct:
        return direct
    template = str((cfg.get("facebook", {}) or {}).get("first_comment", "") or "").strip()
    if not template:
        return ""
    return render_template(
        template,
        {
            "title": " ".join(str((clip or {}).get("title", "")).split()),
            "footer": str((clip or {}).get("footer", "")),
            "topic": str((clip or {}).get("topic", "")),
            "caption": str((clip or {}).get("caption", "")),
        },
    )


def post_first_comment(
    video_id: str, token: str, version: str, message: str, retries: int
) -> dict:
    """Đăng comment đầu tiên dưới Reel (Page tự comment).

    @param video_id id video vừa publish
    @param message nội dung comment
    @param retries số lần thử khi lỗi tạm
    @returns response của Graph
    """
    url = f"https://graph.facebook.com/{version}/{video_id}/comments"
    resp = request_with_retry(
        "POST", url, retries, data={"message": message, "access_token": token}, timeout=60
    )
    if resp.status_code >= 400:
        raise api_error(resp)
    return resp.json()



def caption_from_json(data: dict, extra_tags: str) -> str:
    """Caption Facebook: `caption` trong JSON, thiếu thì sinh từ nội dung, rồi ghép hashtag."""
    from schema import compose_caption, default_caption

    caption = (data.get("caption") or "").strip() or default_caption(data)
    return compose_caption(caption, extra_tags)


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
    dry_run: bool = False,
    clip: dict | None = None,
    first_comment: str | None = None,
) -> dict:
    """Đăng 1 video lên Page dưới dạng Reel.

    @param clip_id id clip để chống đăng trùng, rỗng thì bỏ qua log
    @param force đăng lại dù đã có trong log / trùng nội dung
    @param dry_run chỉ kiểm tra + in kế hoạch request, không gọi mạng
    @param clip clip đã validate, dùng để lưu chữ ký nội dung + topic
    @param first_comment None = lấy từ clip/config, "" = không đăng comment đầu
    @returns response của bước finish kèm video_id/reel_url (hoặc kế hoạch khi dry_run)
    """
    cfg = load_config()
    retries = int(cfg.facebook.max_retries)
    duration = probe_duration(video_path)
    lo, hi = float(cfg.video.min_seconds), float(cfg.video.max_seconds)
    if duration < lo or duration > hi:
        raise SystemExit(f"Video {duration:.1f}s ngoài khoảng Reels {lo:.0f}–{hi:.0f}s")
    if clip_id:
        assert_can_publish(clip_id, force=force, data=clip)
    log(f"Quota còn {remaining_quota()}/{cfg.facebook.daily_limit} trong 24h")
    thumb_offset = int((cfg.get("facebook", {}) or {}).get("thumb_offset_ms", 0) or 0)
    comment = first_comment_text(clip, cfg) if first_comment is None else first_comment
    if state != "PUBLISHED":
        comment = ""
    if dry_run:
        plan = {
            "target": "facebook",
            "dry_run": True,
            "endpoint": f"https://graph.facebook.com/{version}/{page_id or '{FB_PAGE_ID}'}"
                        "/video_reels",
            "phases": ["start", "upload", "wait_ready", "finish"],
            "video": str(video_path),
            "video_bytes": video_path.stat().st_size if video_path.exists() else 0,
            "duration": round(duration, 2),
            "state": state,
            "scheduled_ts": scheduled_ts,
            "title": title,
            "caption": description,
            "caption_chars": len(description),
            "thumb_offset_ms": thumb_offset,
            "first_comment": comment,
            "quota_remaining": remaining_quota(),
            "has_page_id": bool(page_id),
            "has_token": bool(token),
        }
        log(f"[dry-run] không gọi mạng: {plan['video_bytes']} byte, {plan['duration']:.2f}s, "
            f"state={state}, caption {plan['caption_chars']} ký tự"
            + (f", comment đầu {len(comment)} ký tự" if comment else ", không comment đầu"))
        return plan
    log("1) START session")
    video_id, upload_url = start_session(page_id, token, version, retries)
    log(f"   video_id={video_id}")
    log("2) UPLOAD file")
    upload_binary(upload_url, token, video_path, retries)
    log("3) WAIT encode")
    wait_ready(video_id, token, version)
    log(f"4) FINISH state={state}")
    result = finish_publish(
        page_id, token, version, video_id, description, title, state, scheduled_ts, retries,
        thumb_offset_ms=thumb_offset,
    )
    result["video_id"] = video_id
    result["reel_url"] = f"https://www.facebook.com/reel/{video_id}"
    if comment:
        try:
            result["first_comment"] = post_first_comment(video_id, token, version, comment, retries)
            log("5) COMMENT đầu tiên: đã đăng")
        except (SystemExit, Exception) as exc:  # Reel đã publish: comment lỗi không làm job fail
            result["first_comment_error"] = str(exc)
            log(f"cảnh báo: không đăng được comment đầu ({exc})")
    if clip_id:
        record_publish(
            clip_id,
            video_id,
            state,
            result["reel_url"],
            {"title": title, "topic": str((clip or {}).get("topic", ""))},
            data=clip,
        )
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
    parser.add_argument("--dry-run", action="store_true",
                        help="In kế hoạch request, không gọi mạng (không cần token)")
    parser.add_argument("--no-first-comment", action="store_true", help="Không đăng comment đầu")
    args = parser.parse_args()

    page_id = os.getenv("FB_PAGE_ID", "").strip()
    token = os.getenv("FB_PAGE_ACCESS_TOKEN", "").strip()
    version = os.getenv("FB_API_VERSION", cfg.facebook.api_version).strip() or DEFAULT_VERSION
    tags = os.getenv("DEFAULT_HASHTAGS", "").strip()
    if not args.dry_run and (not page_id or not token):
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
        dry_run=args.dry_run,
        clip=data or None,
        first_comment="" if args.no_first_comment else None,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
