#!/usr/bin/env python3
"""Publish log + rolling 24h rate limit (30 Reels / Page)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from config import load_config, resolve_path


def log_path() -> Path:
    cfg = load_config()
    return resolve_path(cfg.paths.published_log)


def load_log() -> dict[str, Any]:
    path = log_path()
    if not path.exists():
        return {"posts": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"posts": []}


def save_log(data: dict[str, Any]) -> None:
    path = log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def already_published(clip_id: str) -> dict[str, Any] | None:
    for post in load_log().get("posts", []):
        if post.get("clip_id") == clip_id and post.get("state") == "PUBLISHED":
            return post
    return None


def posts_last_24h() -> list[dict[str, Any]]:
    cutoff = time.time() - 24 * 3600
    return [p for p in load_log().get("posts", []) if float(p.get("ts", 0)) >= cutoff]


def remaining_quota() -> int:
    cfg = load_config()
    limit = int(cfg.facebook.daily_limit)
    return max(0, limit - len(posts_last_24h()))


def assert_can_publish(clip_id: str, force: bool = False) -> None:
    cfg = load_config()
    limit = int(cfg.facebook.daily_limit)
    used = len(posts_last_24h())
    if used >= limit:
        raise SystemExit(f"Đã đủ {limit} Reels / 24h. Đợi hoặc xem data/published.json.")
    if not force:
        prev = already_published(clip_id)
        if prev:
            raise SystemExit(
                f"Clip {clip_id} đã đăng (video_id={prev.get('video_id')}). "
                f"Dùng --force nếu muốn đăng lại."
            )


def record_publish(clip_id: str, video_id: str, state: str, url: str, extra: dict | None = None) -> None:
    data = load_log()
    posts = data.setdefault("posts", [])
    entry = {
        "clip_id": clip_id,
        "video_id": video_id,
        "state": state,
        "url": url,
        "ts": time.time(),
        "iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    if extra:
        entry.update(extra)
    posts.append(entry)
    save_log(data)
