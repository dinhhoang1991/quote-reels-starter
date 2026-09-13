#!/usr/bin/env python3
"""Validate list JSON before render / upload, and fingerprint its content."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Any

REQUIRED = ("id", "title", "items")
ITEM_REQUIRED = ("label", "text")
ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")

# Luật biên tập trong prompts/generate_list.md. Vượt thì cảnh báo, không chặn render.
CONTENT_LIMITS = {
    "title_words": 8,
    "title_lines": 2,
    "label_words": 6,
    "text_words": 8,
    "min_items": 8,
    "max_items": 10,
    "first_comment_chars": 200,
    "tiktok_caption_chars": 2200,
    "youtube_caption_chars": 5000,
}
NON_WORD_RE = re.compile(r"[^a-z0-9 ]+")


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
    data["first_comment"] = str(data.get("first_comment") or "").strip()
    data["youtube_caption"] = str(data.get("youtube_caption") or "").strip()
    data["tiktok_caption"] = str(data.get("tiktok_caption") or "").strip()
    if not data["voice_script"]:
        data["voice_script"] = default_voice_script(data)
    if not data["caption"]:
        data["caption"] = default_caption(data)
    return data


def content_warnings(data: dict[str, Any]) -> list[str]:
    """Cảnh báo nội dung vượt luật biên tập — render vẫn chạy, chỉ nhắc người viết.

    @param data clip đã qua validate_clip
    @returns danh sách cảnh báo dạng người đọc được, rỗng nếu nội dung đúng luật
    """
    warnings: list[str] = []
    title_lines = [line for line in str(data.get("title", "")).split("\n") if line.strip()]
    title_words = len(" ".join(title_lines).split())
    if len(title_lines) > CONTENT_LIMITS["title_lines"]:
        warnings.append(
            f"title có {len(title_lines)} dòng, khuyến nghị tối đa "
            f"{CONTENT_LIMITS['title_lines']}"
        )
    if title_words > CONTENT_LIMITS["title_words"]:
        warnings.append(
            f"title có {title_words} chữ, khuyến nghị tối đa {CONTENT_LIMITS['title_words']}"
        )
    items = data.get("items") or []
    if len(items) < CONTENT_LIMITS["min_items"]:
        warnings.append(
            f"chỉ có {len(items)} items, khuyến nghị {CONTENT_LIMITS['min_items']}–"
            f"{CONTENT_LIMITS['max_items']}"
        )
    if len(items) > CONTENT_LIMITS["max_items"]:
        warnings.append(
            f"có {len(items)} items, khuyến nghị tối đa {CONTENT_LIMITS['max_items']}"
        )
    long_labels: list[int] = []
    long_texts: list[int] = []
    for idx, item in enumerate(items, start=1):
        if len(str(item.get("label", "")).split()) > CONTENT_LIMITS["label_words"]:
            long_labels.append(idx)
        if len(str(item.get("text", "")).split()) > CONTENT_LIMITS["text_words"]:
            long_texts.append(idx)
    first_comment = str(data.get("first_comment") or "")
    if len(first_comment) > CONTENT_LIMITS["first_comment_chars"]:
        warnings.append(
            f"first_comment dài {len(first_comment)} ký tự, khuyến nghị tối đa "
            f"{CONTENT_LIMITS['first_comment_chars']} (1–2 câu)"
        )
    for field, limit in (("tiktok_caption", "tiktok_caption_chars"),
                         ("youtube_caption", "youtube_caption_chars")):
        value = str(data.get(field) or "")
        if len(value) > CONTENT_LIMITS[limit]:
            warnings.append(f"{field} dài {len(value)} ký tự, sẽ bị cắt còn {CONTENT_LIMITS[limit]}")

    if long_labels:
        warnings.append(
            f"{len(long_labels)} item có label quá {CONTENT_LIMITS['label_words']} chữ "
            f"({_item_list(long_labels)})"
        )
    if long_texts:
        warnings.append(
            f"{len(long_texts)} item có text quá {CONTENT_LIMITS['text_words']} chữ "
            f"({_item_list(long_texts)})"
        )
    return warnings


def _item_list(indexes: list[int], limit: int = 5) -> str:
    """Gọn danh sách số thứ tự item cho cảnh báo: '1, 2, 3 … (+4)'."""
    shown = ", ".join(str(i) for i in indexes[:limit])
    hidden = len(indexes) - limit
    return f"item {shown} … (+{hidden})" if hidden > 0 else f"item {shown}"


def normalize_text(text: str) -> str:
    """Chữ thường, bỏ dấu tiếng Việt, bỏ ký tự lạ — dùng để so trùng nội dung.

    @param text chuỗi bất kỳ
    @returns chuỗi đã chuẩn hoá, khoảng trắng gộp còn 1
    """
    decomposed = unicodedata.normalize("NFD", str(text).lower().replace("đ", "d"))
    without_marks = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(NON_WORD_RE.sub(" ", without_marks).split())


def content_tokens(data: dict[str, Any]) -> list[str]:
    """Các đơn vị nội dung để so trùng: tiêu đề + từng label + từng text.

    @param data clip đã validate
    @returns danh sách chuỗi đã chuẩn hoá, bỏ phần rỗng
    """
    parts = [normalize_text(data.get("title", ""))]
    for item in data.get("items") or []:
        parts.append(normalize_text(item.get("label", "")))
        parts.append(normalize_text(item.get("text", "")))
    return [part for part in parts if part]


def content_similarity(left: list[str], right: list[str]) -> float:
    """Tỉ lệ trùng nội dung theo Jaccard trên tập token.

    @param left token của clip A
    @param right token của clip B
    @returns 0.0–1.0; 1.0 khi hai tập giống nhau, 0.0 khi rời nhau hoặc rỗng
    """
    first, second = set(left), set(right)
    if not first or not second:
        return 0.0
    return len(first & second) / len(first | second)


def content_signature(data: dict[str, Any]) -> dict[str, Any]:
    """Chữ ký nội dung lưu vào published log để chặn đăng trùng về sau.

    @param data clip đã validate
    @returns {"fingerprint": sha256 16 ký tự, "tokens": [...]}
    """
    tokens = content_tokens(data)
    digest = hashlib.sha256("|".join(tokens).encode("utf-8")).hexdigest()[:16]
    return {"fingerprint": digest, "tokens": tokens}


def default_voice_script(data: dict[str, Any]) -> str:
    title = " ".join(data["title"].split())
    parts = [title + "."]
    for item in data["items"]:
        parts.append(f"{item['label']}: {item['text']}.")
    if data.get("footer"):
        parts.append(str(data["footer"]))
    return " ".join(parts)


def compose_caption(base: str, extra: str = "") -> str:
    """Ghép caption với hashtag phụ, không thêm trùng.

    @param base caption chính
    @param extra hashtag/đuôi thêm vào
    @returns caption đã ghép
    """
    text = str(base).strip()
    extra = str(extra).strip()
    if extra and extra not in text:
        text = f"{text}\n\n{extra}".strip()
    return text


def default_caption(data: dict[str, Any]) -> str:
    title = " ".join(data["title"].split())
    lines = [title]
    for idx, item in enumerate(data["items"], start=1):
        lines.append(f"{idx}. {item['label']}: {item['text']}")
    if data.get("footer"):
        lines.append("")
        lines.append(str(data["footer"]))
    return "\n".join(lines)
