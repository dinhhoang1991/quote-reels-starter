#!/usr/bin/env python3
"""Publish log + rolling 24h rate limit (30 Reels / Page) + log có timestamp."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from config import load_config, resolve_path
from schema import content_signature, content_similarity

PUBLISHED_STATE = "PUBLISHED"
DEFAULT_SIMILARITY = 0.85


def log(message: str) -> None:
    """In 1 dòng log có timestamp UTC để đọc lại log cron dễ dàng."""
    print(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {message}", flush=True)


def log_path() -> Path:
    cfg = load_config()
    return resolve_path(cfg.paths.published_log)


def load_log() -> dict[str, Any]:
    path = log_path()
    if not path.exists():
        return {"posts": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"posts": []}
    return data if isinstance(data, dict) else {"posts": []}


def save_log(data: dict[str, Any]) -> None:
    """Ghi log qua file tạm rồi os.replace để cron chạy chồng không làm hỏng file."""
    path = log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def already_published(clip_id: str) -> dict[str, Any] | None:
    for post in load_log().get("posts", []):
        if post.get("clip_id") == clip_id and post.get("state") == PUBLISHED_STATE:
            return post
    return None


def crosspost_state(target: str) -> str:
    """Trạng thái log cho một lần cross-post: `CROSSPOST_YOUTUBE`."""
    return f"CROSSPOST_{str(target).upper()}"


def already_crossposted(clip_id: str, target: str) -> dict[str, Any] | None:
    """Lần cross-post trước đó của clip lên nền tảng này, nếu có.

    @param clip_id id clip
    @param target 'youtube' | 'tiktok'
    @returns entry trong published log hoặc None
    """
    state = crosspost_state(target)
    for post in load_log().get("posts", []):
        if post.get("clip_id") == clip_id and str(post.get("state", "")).upper() == state:
            return post
    return None


def posts_last_24h(states: tuple[str, ...] = (PUBLISHED_STATE,)) -> list[dict[str, Any]]:
    """Các lần đăng trong 24h qua. Mặc định chỉ tính bài đã publish thật.

    Bản DRAFT không tiêu tốn hạn mức 30 Reels/24h của Page nên không bị trừ oan.

    @param states các trạng thái được tính vào hạn mức
    @returns danh sách entry trong data/published.json
    """
    cutoff = time.time() - 24 * 3600
    wanted = {state.upper() for state in states}
    return [
        post
        for post in load_log().get("posts", [])
        if float(post.get("ts", 0)) >= cutoff and str(post.get("state", "")).upper() in wanted
    ]


def remaining_quota() -> int:
    cfg = load_config()
    limit = int(cfg.facebook.daily_limit)
    return max(0, limit - len(posts_last_24h()))


def duplicate_content(data: dict[str, Any], threshold: float | None = None) -> dict[str, Any] | None:
    """Bài đã publish có nội dung trùng hoặc quá giống clip này.

    So fingerprint trước (trùng khít), rồi tới Jaccard trên token nội dung.

    @param data clip đã validate
    @param threshold ngưỡng giống nhau 0–1, mặc định `content.duplicate_similarity`; <=0 là tắt
    @returns entry trong published log bị coi là trùng, None nếu không
    """
    cfg = load_config()
    if threshold is None:
        threshold = float(cfg.get("content", {}).get("duplicate_similarity", DEFAULT_SIMILARITY))
    if threshold <= 0:
        return None
    signature = content_signature(data)
    tokens = signature["tokens"]
    for post in load_log().get("posts", []):
        if str(post.get("state", "")).upper() != PUBLISHED_STATE:
            continue
        stored = post.get("content") or {}
        if stored.get("fingerprint") and stored["fingerprint"] == signature["fingerprint"]:
            return post
        similarity = content_similarity(tokens, list(stored.get("tokens") or []))
        if similarity >= threshold:
            post = dict(post)
            post["_similarity"] = round(similarity, 3)
            return post
    return None


def assert_can_publish(
    clip_id: str, force: bool = False, data: dict[str, Any] | None = None
) -> None:
    """Chặn khi hết hạn mức 24h, khi clip đã đăng, hoặc khi nội dung quá giống bài cũ.

    @param clip_id id clip
    @param force bỏ qua các kiểm tra chống trùng
    @param data clip đã validate (cần để so trùng nội dung)
    @raises SystemExit khi không được đăng
    """
    cfg = load_config()
    limit = int(cfg.facebook.daily_limit)
    used = len(posts_last_24h())
    if used >= limit:
        raise SystemExit(f"Đã đủ {limit} Reels / 24h. Đợi hoặc xem data/published.json.")
    if force:
        return
    prev = already_published(clip_id)
    if prev:
        raise SystemExit(
            f"Clip {clip_id} đã đăng (video_id={prev.get('video_id')}). "
            f"Dùng --force nếu muốn đăng lại."
        )
    if not data:
        return
    duplicate = duplicate_content(data)
    if duplicate:
        similar = duplicate.get("_similarity")
        how = f"giống {similar:.0%}" if similar is not None else "trùng nội dung"
        raise SystemExit(
            f"Clip {data.get('id', clip_id)} {how} với bài đã đăng "
            f"{duplicate.get('clip_id')} ({duplicate.get('url')}). "
            f"Dùng --force nếu vẫn muốn đăng."
        )


def record_publish(
    clip_id: str,
    video_id: str,
    state: str,
    url: str,
    extra: dict | None = None,
    data: dict[str, Any] | None = None,
) -> None:
    """Ghi 1 lần đăng vào data/published.json.

    @param clip_id id clip
    @param video_id id video trên Facebook
    @param state PUBLISHED | DRAFT | ...
    @param url link reel
    @param extra field phụ (title, topic…)
    @param data clip đã validate, dùng để lưu chữ ký nội dung chống trùng
    """
    log_data = load_log()
    posts = log_data.setdefault("posts", [])
    entry = {
        "clip_id": clip_id,
        "video_id": video_id,
        "state": state,
        "url": url,
        "ts": time.time(),
        "iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    if data:
        entry["content"] = content_signature(data)
    if extra:
        entry.update(extra)
    posts.append(entry)
    save_log(log_data)
