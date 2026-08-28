#!/usr/bin/env python3
"""Đổi short-lived User token → long-lived (~60 ngày).

Rồi lấy Page token từ /me/accounts. Page token từ long-lived user token
cũng sống ~60 ngày. Production: System User trong Business Manager.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from config import root

ROOT = root()


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def exchange(app_id: str, app_secret: str, short_token: str, version: str) -> dict:
    try:
        import requests
    except ImportError as exc:
        raise SystemExit("Chưa cài requests. Chạy: pip install requests") from exc

    url = f"https://graph.facebook.com/{version}/oauth/access_token"
    resp = requests.get(
        url,
        params={
            "grant_type": "fb_exchange_token",
            "client_id": app_id,
            "client_secret": app_secret,
            "fb_exchange_token": short_token,
        },
        timeout=30,
    )
    data = resp.json()
    if resp.status_code >= 400 or "access_token" not in data:
        raise SystemExit(f"Đổi token thất bại: {json.dumps(data, ensure_ascii=False)}")
    return data


def list_pages(user_token: str, version: str) -> list[dict]:
    try:
        import requests
    except ImportError as exc:
        raise SystemExit("Chưa cài requests") from exc
    resp = requests.get(
        f"https://graph.facebook.com/{version}/me/accounts",
        params={"access_token": user_token, "fields": "id,name,access_token,tasks"},
        timeout=30,
    )
    data = resp.json()
    if resp.status_code >= 400:
        raise SystemExit(f"me/accounts lỗi: {json.dumps(data, ensure_ascii=False)}")
    return data.get("data") or []


def main() -> None:
    load_env(ROOT / ".env")
    parser = argparse.ArgumentParser(description="Gia hạn Facebook token")
    parser.add_argument("--short", default="", help="Short-lived user token")
    parser.add_argument("--list-pages", action="store_true")
    args = parser.parse_args()

    version = os.getenv("FB_API_VERSION", "v26.0")
    app_id = os.getenv("FB_APP_ID", "").strip()
    app_secret = os.getenv("FB_APP_SECRET", "").strip()
    short = (args.short or os.getenv("FB_SHORT_TOKEN", "")).strip()

    if args.list_pages:
        token = os.getenv("FB_PAGE_ACCESS_TOKEN", "").strip() or short
        if not token:
            raise SystemExit("Thiếu token để gọi /me/accounts")
        pages = list_pages(token, version)
        print(json.dumps(pages, ensure_ascii=False, indent=2))
        return

    if not app_id or not app_secret or not short:
        raise SystemExit("Cần FB_APP_ID, FB_APP_SECRET và --short TOKEN (hoặc FB_SHORT_TOKEN)")

    result = exchange(app_id, app_secret, short, version)
    print("Long-lived user token:")
    print(result.get("access_token"))
    expires = result.get("expires_in")
    if expires:
        days = int(expires) / 86400
        print(f"expires_in={expires}s (~{days:.0f} ngày)")
    print("\nPage tokens từ user token này:")
    pages = list_pages(result["access_token"], version)
    print(json.dumps(pages, ensure_ascii=False, indent=2))
    print("\nCopy access_token của đúng Page vào FB_PAGE_ACCESS_TOKEN trong .env")
    print("App phải ở chế độ Live (hoặc user là tester) mới đăng được cho người ngoài.")


if __name__ == "__main__":
    main()
