#!/usr/bin/env python3
"""Cảnh báo khi pipeline lỗi: Telegram bot và/hoặc webhook chung.

    python3 src/notify.py "nội dung thử"     # kiểm tra cấu hình cảnh báo

Cấu hình trong .env (bật kênh nào thì khai báo kênh đó):
    TELEGRAM_BOT_TOKEN=...   TELEGRAM_CHAT_ID=...
    NOTIFY_WEBHOOK_URL=https://...        # POST JSON {"text": ..., "level": ...}
    NOTIFY_LEVEL=warn                     # info | warn | error — mức tối thiểu để gửi (mặc định warn)

Nguyên tắc: cảnh báo KHÔNG bao giờ làm hỏng pipeline — mọi lỗi gửi chỉ được log lại.
"""

from __future__ import annotations

import argparse
import json
import os
from typing import Any

from config import Cfg, load_config, root
from logutil import log

ROOT = root()
TELEGRAM_API = "https://api.telegram.org"
TIMEOUT = 10
LEVELS = ("info", "warn", "error")
LEVEL_RANK = {level: rank for rank, level in enumerate(LEVELS)}
TAG = {"info": "ℹ️", "warn": "⚠️", "error": "❌"}


def load_notify_env(cfg: Cfg | None = None) -> dict[str, Any]:
    """Cấu hình cảnh báo từ .env + config.

    @param cfg config, mặc định đọc config.yaml
    @returns {"enabled", "min_level", "on_success", "on_empty_queue",
              "telegram_token", "telegram_chat_id", "webhook"}
    """
    from upload_facebook import load_env

    load_env(ROOT / ".env")
    cfg = cfg or load_config()
    section = cfg.get("notify", {}) or {}
    return {
        "enabled": bool(section.get("enabled", True)),
        "min_level": str(os.getenv("NOTIFY_LEVEL") or section.get("min_level", "warn")).lower(),
        "on_success": bool(section.get("on_success", False)),
        "on_empty_queue": bool(section.get("on_empty_queue", True)),
        "telegram_token": os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
        "telegram_chat_id": os.getenv("TELEGRAM_CHAT_ID", "").strip(),
        "webhook": os.getenv("NOTIFY_WEBHOOK_URL", "").strip(),
    }


def format_message(message: str, level: str = "warn", clip_id: str = "") -> str:
    """Soạn nội dung cảnh báo.

    @param message nội dung chính
    @param level info | warn | error
    @param clip_id id clip liên quan, rỗng thì bỏ
    @returns chuỗi 1 dòng có tag mức độ
    """
    prefix = TAG.get(level, TAG["warn"])
    where = f"[{clip_id}] " if clip_id else ""
    return f"{prefix} {where}{' '.join(str(message).split())}"


def telegram_payload(chat_id: str, text: str) -> dict:
    """Body cho Telegram sendMessage."""
    return {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}


def should_send(level: str, min_level: str) -> bool:
    """Mức của thông báo có đạt ngưỡng gửi hay không."""
    return LEVEL_RANK.get(level, 1) >= LEVEL_RANK.get(min_level, 1)


def notify(
    message: str,
    level: str = "warn",
    clip_id: str = "",
    cfg: Cfg | None = None,
    dry_run: bool = False,
    min_level: str | None = None,
) -> dict[str, dict]:
    """Gửi cảnh báo tới các kênh đã cấu hình.

    @param message nội dung
    @param level info | warn | error
    @param clip_id id clip liên quan
    @param cfg config, mặc định đọc config.yaml
    @param dry_run chỉ in ra, không gọi mạng
    @param min_level ghi đè ngưỡng trong config (dùng cho thông báo đã opt-in như on_success)
    @returns {kênh: {"ok": True} | {"error": "..."}}; rỗng nếu không gửi gì
    """
    settings = load_notify_env(cfg)
    text = format_message(message, level, clip_id)
    if not settings["enabled"]:
        log(f"cảnh báo tắt trong config: {text}")
        return {}
    if not should_send(level, str(min_level or settings["min_level"])):
        return {}
    channels = {
        "telegram": bool(settings["telegram_token"] and settings["telegram_chat_id"]),
        "webhook": bool(settings["webhook"]),
    }
    if not any(channels.values()):
        log(f"chưa cấu hình cảnh báo (TELEGRAM_BOT_TOKEN/CHAT_ID hoặc NOTIFY_WEBHOOK_URL): {text}")
        return {}
    if dry_run:
        log(f"[dry-run] {text}")
        return {name: {"ok": True, "dry_run": True} for name, on in channels.items() if on}

    results: dict[str, dict] = {}
    if channels["telegram"]:
        url = f"{TELEGRAM_API}/bot{settings['telegram_token']}/sendMessage"
        results["telegram"] = _post(
            url, json_body=telegram_payload(str(settings["telegram_chat_id"]), text)
        )
    if channels["webhook"]:
        results["webhook"] = _post(
            str(settings["webhook"]), json_body={"text": text, "level": level, "clip_id": clip_id}
        )
    sent = [name for name, result in results.items() if result.get("ok")]
    if sent:
        log(f"đã gửi cảnh báo qua {', '.join(sent)}: {text}")
    return results


def _post(url: str, json_body: dict) -> dict:
    """POST JSON, mọi lỗi đều bị bắt để không làm hỏng pipeline."""
    try:
        import requests

        resp = requests.post(url, json=json_body, timeout=TIMEOUT)
    except (SystemExit, Exception) as exc:  # mạng lỗi, thiếu requests, DNS…
        log(f"cảnh báo: không gửi được tới {url.split('?')[0]}: {exc}")
        return {"error": str(exc)}
    if resp.status_code >= 400:
        detail = resp.text[:200]
        log(f"cảnh báo: kênh trả {resp.status_code}: {detail}")
        return {"error": f"HTTP {resp.status_code}: {detail}"}
    return {"ok": True}


def notify_publish_failure(clip_id: str, error: str) -> dict[str, dict]:
    """Cảnh báo khi một job publish thất bại."""
    return notify(f"Publish lỗi: {error}", level="error", clip_id=clip_id)


def notify_publish_success(clip_id: str, url: str = "") -> dict[str, dict]:
    """Cảnh báo khi publish thành công (chỉ gửi nếu config bật on_success)."""
    settings = load_notify_env()
    if not settings["on_success"]:
        return {}
    # Thông báo thành công là opt-in, nên không bị ngưỡng min_level (mặc định warn) chặn.
    return notify(
        f"Đã đăng Reel: {url}" if url else "Đã đăng Reel",
        level="info", clip_id=clip_id, min_level="info",
    )


def notify_empty_queue() -> dict[str, dict]:
    """Cảnh báo khi hàng chờ trống (chỉ gửi nếu config bật on_empty_queue)."""
    settings = load_notify_env()
    if not settings["on_empty_queue"]:
        return {}
    return notify("Hàng chờ trống — cần thêm clip vào data/queue/pending/", level="warn")


def notify_token_expiring(days: float) -> dict[str, dict]:
    """Cảnh báo token Facebook sắp hết hạn."""
    return notify(f"Facebook token còn {days:.1f} ngày — chạy src/fbtoken.py để gia hạn",
                  level="warn")


def main() -> None:
    cfg = load_config()
    parser = argparse.ArgumentParser(description="Gửi cảnh báo thử")
    parser.add_argument("message", nargs="?", default="Tin nhắn thử từ quote-reels-starter")
    parser.add_argument("--level", default="warn", choices=LEVELS)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    settings = load_notify_env(cfg)
    channels = [
        name for name, on in (
            ("telegram", bool(settings["telegram_token"] and settings["telegram_chat_id"])),
            ("webhook", bool(settings["webhook"])),
        ) if on
    ]
    log(f"kênh đã cấu hình: {', '.join(channels) if channels else 'chưa có'}")
    result = notify(args.message, level=args.level, cfg=cfg, dry_run=args.dry_run)
    print(json.dumps(dict(result), ensure_ascii=False))


if __name__ == "__main__":
    main()
