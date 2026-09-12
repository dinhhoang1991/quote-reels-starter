#!/usr/bin/env python3
"""Preflight trước khi render/đăng: môi trường, asset, config, hàng chờ, token, 1 clip.

    python3 src/doctor.py                                  # môi trường + asset + config
    python3 src/doctor.py --json data/samples/clip_001.json # thêm kiểm tra 1 clip
    python3 src/doctor.py --online                          # gọi Graph debug_token (cần .env)

Mỗi dòng in ra `[ok]`, `[warn]` hoặc `[fail]`. Exit code 1 nếu có `[fail]`.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from checks import estimate_voice_seconds
from config import Cfg, load_config, resolve_path, root
from logutil import log
from make_video import ken_burns_plan
from render_overlay import OverlayFitError, render_pair
from schema import ClipError, content_warnings, load_clip
from tts import voice_path

ROOT = root()
OK = "ok"
WARN = "warn"
FAIL = "fail"
REQUIRED_MODULES = ("PIL", "yaml", "requests")
OPTIONAL_MODULES = ("edge_tts",)
TOKEN_WARN_DAYS = 7.0


@dataclass
class Check:
    """Một kết quả kiểm tra.

    @param level ok | warn | fail
    @param name tên hạng mục
    @param detail chi tiết người đọc được
    """

    level: str
    name: str
    detail: str = ""


def module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def check_environment() -> list[Check]:
    """Python, thư viện Python, ffmpeg/ffprobe."""
    checks: list[Check] = []
    version = ".".join(str(part) for part in sys.version_info[:3])
    # `requires-python` đã khai báo >=3.10, nhưng người chạy bằng interpreter cũ vẫn cần biết lý do.
    if sys.version_info >= (3, 10):  # noqa: UP036
        checks.append(Check(OK, "python", version))
    else:
        checks.append(Check(FAIL, "python", f"{version} — cần 3.10+"))
    for name in REQUIRED_MODULES:
        available = module_available(name)
        checks.append(
            Check(OK if available else FAIL, f"lib {name}",
                  "" if available else "thiếu, chạy: pip install -r requirements.txt")
        )
    for name in OPTIONAL_MODULES:
        available = module_available(name)
        checks.append(
            Check(OK if available else WARN, f"lib {name}",
                  "" if available else "thiếu, chỉ cần khi để script tự đọc voice_script")
        )
    ffmpeg = shutil.which("ffmpeg")
    checks.append(Check(OK if ffmpeg else FAIL, "ffmpeg", ffmpeg or "thiếu, cài FFmpeg rồi chạy lại"))
    ffprobe = shutil.which("ffprobe")
    checks.append(
        Check(OK if ffprobe else WARN, "ffprobe",
              ffprobe or "thiếu — sẽ đọc duration bằng ffmpeg (chậm hơn)")
    )
    return checks


def _writable_dir(path: Path, name: str) -> Check:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return Check(FAIL, name, f"{path}: {exc}")
    return Check(OK if os.access(path, os.W_OK) else FAIL, name, str(path))


def _media_files(folder: Path, exts: tuple[str, ...]) -> list[Path]:
    if not folder.exists():
        return []
    return [
        path
        for path in sorted(folder.iterdir())
        if path.is_file() and path.suffix.lower() in exts and not path.name.startswith("_placeholder")
    ]


def check_assets(cfg: Cfg) -> list[Check]:
    """Font bắt buộc, footage/nhạc thật, thư mục ghi được."""
    checks: list[Check] = []
    fonts_dir = resolve_path(cfg.paths.fonts_dir)
    missing = [
        name
        for name in (cfg.paths.title_font, cfg.paths.body_font, cfg.paths.footer_font)
        if not (fonts_dir / name).exists()
    ]
    checks.append(
        Check(OK if not missing else FAIL, "fonts",
              str(fonts_dir) if not missing else f"thiếu {', '.join(missing)}")
    )

    footage = _media_files(resolve_path(cfg.paths.footage_dir), (".mp4", ".mov", ".mkv", ".webm",
                                                                 ".jpg", ".jpeg", ".png", ".webp"))
    checks.append(
        Check(OK if footage else WARN, "footage",
              f"{len(footage)} file, dùng {footage[0].name}" if footage
              else "trống — sẽ tự sinh nền gradient (đừng đăng bản này)")
    )
    music = _media_files(resolve_path(cfg.paths.music_dir), (".mp3", ".wav", ".m4a", ".aac"))
    checks.append(
        Check(OK if music else WARN, "music",
              f"{len(music)} file, dùng {music[0].name}" if music
              else "trống — sẽ tự sinh brown noise (đừng đăng bản này)")
    )
    for key, name in (("overlay_dir", "assets/overlays"), ("out_dir", "assets/out"),
                      ("voice_dir", "assets/voice")):
        checks.append(_writable_dir(resolve_path(cfg.paths[key]), name))
    return checks


def check_config(cfg: Cfg) -> list[Check]:
    """Các giá trị mà render/upload sẽ dùng."""
    checks: list[Check] = []
    width, height = int(cfg.video.width), int(cfg.video.height)
    if abs(width / height - 9 / 16) < 0.001:
        checks.append(Check(OK, "video size", f"{width}x{height} 9:16"))
    else:
        checks.append(Check(WARN, "video size", f"{width}x{height} không phải 9:16"))

    lo, hi = float(cfg.video.min_seconds), float(cfg.video.max_seconds)
    if lo >= hi:
        checks.append(Check(FAIL, "duration", f"min {lo}s >= max {hi}s"))
    elif hi > 90:
        checks.append(Check(WARN, "duration", f"max {hi}s > 90s của Reels API"))
    else:
        checks.append(Check(OK, "duration", f"{lo:.0f}–{hi:.0f}s"))

    top, bottom, side = int(cfg.safe_zone.top), int(cfg.safe_zone.bottom), int(cfg.safe_zone.side)
    checks.append(
        Check(OK if top + bottom < height and side * 2 < width else FAIL, "safe zone",
              f"top {top} / bottom {bottom} / side {side}")
    )

    try:
        plan = ken_burns_plan(cfg, 10.0, int(cfg.video.fps))
    except SystemExit as exc:
        checks.append(Check(FAIL, "ken burns", str(exc.code)))
    else:
        checks.append(
            Check(OK if plan["enabled"] else WARN, "ken burns",
                  f"zoom {plan['zoom_start']}→{plan['zoom_end']}, pan {plan['pan']}"
                  if plan["enabled"] else "đang tắt")
        )

    sr, channels = int(cfg.audio.sample_rate), int(cfg.audio.channels)
    checks.append(
        Check(OK if (sr, channels) == (48000, 2) else WARN, "audio spec",
              f"{sr} Hz / {channels} kênh" + ("" if (sr, channels) == (48000, 2)
                                              else " — Reels API dùng 48 kHz stereo"))
    )
    chars_per_second = float(cfg.audio.get("voice_chars_per_second", 9.2))
    checks.append(
        Check(OK if chars_per_second > 0 else FAIL, "voice estimate",
              f"{chars_per_second} ký tự/giây @ rate 0%, rate {cfg.audio.voice_rate}")
    )
    limit = int(cfg.facebook.daily_limit)
    version = str(cfg.facebook.api_version)
    checks.append(
        Check(OK if limit > 0 and version.startswith("v") else FAIL, "facebook config",
              f"{limit} Reels/24h, API {version}")
    )
    max_delay = float(cfg.facebook.get("retry_max_delay_seconds", 900))
    checks.append(
        Check(OK if max_delay > 0 else FAIL, "retry config",
              f"chờ tối đa {max_delay / 60:.0f} phút mỗi lần thử lại")
    )
    stale = float(cfg.queue.get("stale_lock_seconds", 3600))
    checks.append(
        Check(OK if stale > 0 else FAIL, "queue lock",
              f"lock cũ bị coi là rác sau {stale / 60:.0f} phút")
    )
    return checks


def check_queue(cfg: Cfg) -> list[Check]:
    """Thư mục hàng chờ, log đăng, jobs đã fail, thư mục logs cho cron."""
    checks: list[Check] = []
    base = resolve_path(cfg.paths.queue_dir)
    for name in ("pending", "done", "failed"):
        checks.append(_writable_dir(base / name, f"queue/{name}"))

    failed = sorted((base / "failed").glob("*.json")) if (base / "failed").exists() else []
    if failed:
        detail = f"{len(failed)} job: {', '.join(path.name for path in failed[:3])}"
        checks.append(Check(WARN, "failed jobs", detail + " — xem _queue.last_error"))
    else:
        checks.append(Check(OK, "failed jobs", "trống"))

    log_file = resolve_path(cfg.paths.published_log)
    if not log_file.exists():
        checks.append(Check(OK, "published log", f"{log_file.name} chưa có (sẽ tạo khi đăng)"))
    else:
        try:
            data = json.loads(log_file.read_text(encoding="utf-8"))
            posts = len(data.get("posts", [])) if isinstance(data, dict) else 0
            checks.append(Check(OK, "published log", f"{posts} lần đăng"))
        except (OSError, ValueError) as exc:
            checks.append(Check(FAIL, "published log",
                                f"{log_file}: JSON hỏng ({exc}) — sao lưu rồi xoá để tạo lại"))

    checks.append(_writable_dir(ROOT / "logs", "logs"))
    return checks


def check_clip(cfg: Cfg, data: dict) -> list[Check]:
    """1 clip cụ thể: nội dung, bản đọc, và overlay có dàn được không."""
    checks: list[Check] = []
    for warning in content_warnings(data):
        checks.append(Check(WARN, "content", warning))

    cached = voice_path(data, cfg)
    if cached.exists():
        checks.append(Check(OK, "voice cache", cached.name))
    else:
        checks.append(Check(OK, "voice cache", "chưa có — sẽ gọi edge-tts khi render"))

    estimate = estimate_voice_seconds(
        data.get("voice_script", ""), cfg.audio.voice_rate,
        float(cfg.audio.get("voice_chars_per_second", 9.2)),
    )
    head, tail = float(cfg.audio.head_seconds), float(cfg.audio.tail_seconds)
    hi = float(cfg.video.max_seconds)
    total = estimate + head + tail
    if total > hi:
        checks.append(
            Check(WARN, "voice length",
                  f"~{estimate:.0f}s đọc (+{head + tail:.1f}s đầu/cuối = {total:.0f}s) "
                  f"vượt {hi:.0f}s — video sẽ bị cắt giữa câu")
        )
    else:
        checks.append(Check(OK, "voice length", f"~{estimate:.0f}s đọc, tổng ~{total:.0f}s"))

    fonts_dir = resolve_path(cfg.paths.fonts_dir)
    with tempfile.TemporaryDirectory() as tmp:
        try:
            hook, full = render_pair(data, fonts_dir, Path(tmp))
        except OverlayFitError as exc:
            checks.append(Check(FAIL, "overlay", str(exc)))
        else:
            checks.append(
                Check(OK, "overlay", f"{hook.name} + {full.name} render được")
            )
    return checks


def _debug_token(page_token: str, app_id: str, app_secret: str, version: str) -> dict:
    import requests

    resp = requests.get(
        f"https://graph.facebook.com/{version}/debug_token",
        params={"input_token": page_token, "access_token": f"{app_id}|{app_secret}"},
        timeout=30,
    )
    try:
        payload = resp.json() if resp.content else {}
    except ValueError as exc:
        raise SystemExit(f"debug_token trả về không phải JSON: {resp.text[:200]}") from exc
    if resp.status_code >= 400:
        raise SystemExit(f"debug_token {resp.status_code}: {json.dumps(payload, ensure_ascii=False)}")
    return payload.get("data") or {}


def check_content(cfg: Cfg) -> list[Check]:
    """Chống trùng nội dung, xoay vòng chủ đề và độ mới của insights."""
    from insights import load_insights  # import muộn: kéo theo requests
    from topics import configured_topics, next_topic, rotation_report

    checks: list[Check] = []
    threshold = float((cfg.get("content", {}) or {}).get("duplicate_similarity", 0.85))
    checks.append(
        Check(OK if threshold > 0 else WARN, "chống trùng nội dung",
              f"chặn bài giống từ {threshold:.0%}" if threshold > 0 else "đang tắt (ngưỡng 0)")
    )

    topics = configured_topics(cfg)
    if not topics:
        checks.append(Check(WARN, "chủ đề", "content.topics trống — xoay vòng thủ công"))
    else:
        report = rotation_report(cfg)
        unused = sum(1 for _, count, _ in report if count == 0)
        checks.append(
            Check(OK, "chủ đề",
                  f"{len(topics)} chủ đề, {unused} chưa dùng, tiếp theo: {next_topic(cfg)}")
        )

    data = load_insights()
    videos = data.get("videos") or {}
    fetched = str(data.get("fetched_at") or "chưa cập nhật")
    checks.append(
        Check(OK if videos else WARN, "insights",
              f"{len(videos)} video, cập nhật {fetched}" if videos
              else "chưa có — chạy: python3 src/insights.py refresh")
    )
    return checks


def check_token(cfg: Cfg, online: bool) -> list[Check]:
    """Sự có mặt của .env/token, và khi --online thì hỏi Graph về hạn/quyền của token."""
    checks: list[Check] = []
    env_path = ROOT / ".env"
    if not env_path.exists():
        checks.append(Check(WARN, ".env", "chưa có — copy .env.example rồi điền"))
        return checks

    from upload_facebook import load_env

    load_env(env_path)
    page_id = os.getenv("FB_PAGE_ID", "").strip()
    token = os.getenv("FB_PAGE_ACCESS_TOKEN", "").strip()
    if not page_id or not token:
        checks.append(Check(WARN, "FB token", "thiếu FB_PAGE_ID hoặc FB_PAGE_ACCESS_TOKEN"))
        return checks
    checks.append(Check(OK, "FB token", f"page {page_id}, token {token[:6]}…"))
    if not online:
        checks.append(Check(OK, "FB token online", "bỏ qua (thêm --online để kiểm tra hạn/quyền)"))
        return checks

    app_id = os.getenv("FB_APP_ID", "").strip()
    app_secret = os.getenv("FB_APP_SECRET", "").strip()
    if not app_id or not app_secret:
        checks.append(Check(FAIL, "FB token online", "cần FB_APP_ID + FB_APP_SECRET để gọi debug_token"))
        return checks
    version = os.getenv("FB_API_VERSION", str(cfg.facebook.api_version))
    try:
        data = _debug_token(token, app_id, app_secret, version)
    except SystemExit as exc:
        checks.append(Check(FAIL, "FB token online", str(exc.code)))
        return checks

    if not data.get("is_valid"):
        checks.append(Check(FAIL, "FB token online",
                            "token không hợp lệ/hết hạn — chạy: python3 src/fbtoken.py --short TOKEN"))
        return checks

    expires = float(data.get("expires_at") or 0)
    if expires:
        days = (expires - time.time()) / 86400
        checks.append(
            Check(OK if days > TOKEN_WARN_DAYS else WARN, "FB token hạn",
                  f"còn {days:.1f} ngày" + ("" if days > TOKEN_WARN_DAYS
                                            else " — chạy fbtoken.py để gia hạn"))
        )
    else:
        checks.append(Check(OK, "FB token hạn", "không hết hạn (System User token)"))

    scopes = set(data.get("scopes") or [])
    needed = {"pages_show_list", "pages_read_engagement", "pages_manage_posts"}
    missing = needed - scopes
    checks.append(
        Check(OK if not missing else FAIL, "FB quyền",
              ", ".join(sorted(scopes)) if not missing else f"thiếu {', '.join(sorted(missing))}")
    )
    profile = str(data.get("profile_id") or data.get("user_id") or "").strip()
    if profile and page_id and profile != page_id:
        checks.append(Check(WARN, "FB page", f"token thuộc {profile}, .env ghi {page_id}"))
    else:
        checks.append(Check(OK, "FB page", page_id))
    return checks


def run_all(json_path: Path | None = None, online: bool = False) -> list[Check]:
    """Chạy toàn bộ kiểm tra, gom lại thành 1 danh sách.

    @param json_path clip cần kiểm tra thêm, None thì bỏ qua
    @param online có gọi Graph debug_token hay không
    @returns danh sách Check theo thứ tự chạy
    """
    cfg = load_config()
    checks = check_environment()
    checks += check_assets(cfg)
    checks += check_config(cfg)
    checks += check_content(cfg)
    checks += check_queue(cfg)
    checks += check_token(cfg, online)
    if json_path is not None:
        try:
            data = load_clip(json_path)
        except ClipError as exc:
            checks.append(Check(FAIL, "clip", str(exc)))
        else:
            checks.append(Check(OK, "clip", f"{json_path.name} (id {data['id']})"))
            checks += check_clip(cfg, data)
    return checks


def report(checks: list[Check]) -> int:
    """In kết quả và trả về exit code (1 nếu có fail)."""
    for check in checks:
        line = f"[{check.level}] {check.name}"
        print(f"{line}: {check.detail}" if check.detail else line)
    fails = sum(1 for check in checks if check.level == FAIL)
    warns = sum(1 for check in checks if check.level == WARN)
    log(f"{len(checks)} check — {fails} fail, {warns} warn")
    return 1 if fails else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Preflight cho pipeline Reels")
    parser.add_argument("--json", default="", help="Kiểm tra thêm 1 clip")
    parser.add_argument("--online", action="store_true", help="Gọi Graph debug_token")
    args = parser.parse_args()

    checks = run_all(Path(args.json) if args.json else None, online=args.online)
    raise SystemExit(report(checks))


if __name__ == "__main__":
    main()
