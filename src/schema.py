#!/usr/bin/env python3
"""Validate list JSON before render / upload."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


REQUIRED = ("id", "title", "items")
ITEM_REQUIRED = ("label", "text")
ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")


class ClipError(ValueError):
    pass


def load_clip(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ClipError(f"{path}: JSON lỗi ({exc})") from exc
    return validate_clip(data, source=str(path))


def validate_clip(data: Any, source: str = "clip") -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ClipError(f"{source}: phải là object JSON")
    missing = [k for k in REQUIRED if not data.get(k)]
    if missing:
        raise ClipError(f"{source}: thiếu field {', '.join(missing)}")
    clip_id = str(data["id"]).strip()
    if not ID_RE.match(clip_id):
        raise ClipError(f"{source}: id không hợp lệ (chữ, số, _ -)")
    data["id"] = clip_id
    data["title"] = str(data["title"]).strip()
    if not data["title"]:
        raise ClipError(f"{source}: title trống")
    items = data.get("items")
    if not isinstance(items, list) or not items:
        raise ClipError(f"{source}: items phải là list không rỗng")
    if len(items) > 12:
        raise ClipError(f"{source}: tối đa 12 items (đang {len(items)})")
    cleaned = []
    for i, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise ClipError(f"{source}: item {i} không phải object")
        for key in ITEM_REQUIRED:
            if not str(item.get(key, "")).strip():
                raise ClipError(f"{source}: item {i} thiếu {key}")
        cleaned.append(
            {"label": str(item["label"]).strip().rstrip(":"), "text": str(item["text"]).strip()}
        )
    data["items"] = cleaned
    data["footer"] = str(data.get("footer") or "").strip()
    data["caption"] = str(data.get("caption") or "").strip()
    data["topic"] = str(data.get("topic") or "").strip()
    data["voice_script"] = str(data.get("voice_script") or "").strip()
    if not data["voice_script"]:
        data["voice_script"] = default_voice_script(data)
    if not data["caption"]:
        data["caption"] = default_caption(data)
    return data


def default_voice_script(data: dict[str, Any]) -> str:
    title = " ".join(data["title"].split())
    parts = [title + "."]
    for item in data["items"]:
        parts.append(f"{item['label']}: {item['text']}.")
    if data.get("footer"):
        parts.append(str(data["footer"]))
    return " ".join(parts)


def default_caption(data: dict[str, Any]) -> str:
    title = " ".join(data["title"].split())
    lines = [title]
    for idx, item in enumerate(data["items"], start=1):
        lines.append(f"{idx}. {item['label']}: {item['text']}")
    if data.get("footer"):
        lines.append("")
        lines.append(str(data["footer"]))
    return "\n".join(lines)
