#!/usr/bin/env python3
"""Render 1080x1920 transparent PNG overlays (hook + full) from list JSON.

Respects config.yaml colors, fonts, and Facebook Reels safe zones.
Title and items tự co cỡ chữ cho vừa safe zone; nếu vẫn không vừa thì báo lỗi
thay vì vẽ tràn ra ngoài khung. Khoảng trống còn lại được chia đều giữa các item.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from config import load_config, resolve_path
from schema import content_warnings, load_clip, validate_clip

MIN_TITLE_SIZE = 34
MIN_BODY_SIZE = 26
MAX_ITEM_GAP_BONUS = 36
TITLE_LINE_GAP = 8
ITEM_LINE_GAP = 2
ITEM_ROW_GAP = 14
FOOTER_SIZE = 36
HINT_SIZE = 32


class OverlayFitError(ValueError):
    """Nội dung không vừa safe zone dù đã co chữ tới cỡ nhỏ nhất."""


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def load_font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(path), size=size)


def text_size(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> tuple[int, int]:
    box = draw.textbbox((0, 0), text, font=font, stroke_width=0)
    return box[2] - box[0], box[3] - box[1]


def wrap_lines(
    draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int
) -> list[str]:
    raw = [part.strip() for part in text.split("\n") if part.strip()] or [text]
    lines: list[str] = []
    for part in raw:
        words = part.split() or [part]
        current = ""
        for word in words:
            trial = word if not current else f"{current} {word}"
            w, _ = text_size(draw, trial, font)
            if w <= max_width:
                current = trial
            else:
                if current:
                    lines.append(current)
                current = word
        if current:
            lines.append(current)
    return lines or [text]


def draw_text(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    font: ImageFont.FreeTypeFont,
    fill: tuple[int, int, int, int],
    stroke_width: int = 3,
    stroke_fill: tuple[int, int, int, int] = (0, 0, 0, 220),
    anchor: str = "lt",
) -> None:
    draw.text(
        xy,
        text,
        font=font,
        fill=fill,
        stroke_width=stroke_width,
        stroke_fill=stroke_fill,
        anchor=anchor,
        align="center",
    )


def fit_title(
    draw: ImageDraw.ImageDraw,
    title: str,
    font_path: Path,
    max_width: int,
    max_height: int,
    start_size: int,
) -> tuple[ImageFont.FreeTypeFont, list[str], int, int]:
    """Co cỡ chữ tiêu đề tới khi vừa bề ngang, vừa chiều cao, và giữ đúng số dòng tác giả.

    Ưu tiên cỡ chữ lớn nhất mà tiêu đề không bị wrap thêm dòng; nếu không size nào
    giữ được số dòng thì lấy cỡ lớn nhất còn vừa chiều cao.

    @returns (font, dòng, chiều cao một dòng, tổng chiều cao)
    @raises OverlayFitError khi cỡ chữ nhỏ nhất vẫn không vừa
    """
    size = int(start_size)
    target_lines = max(len([part for part in str(title).split("\n") if part.strip()]), 1)
    last_lines: list[str] = []
    last_h = 0.0
    fallback: tuple[ImageFont.FreeTypeFont, list[str], int, int] | None = None
    while size >= MIN_TITLE_SIZE:
        font = load_font(font_path, size)
        lines = wrap_lines(draw, title, font, max_width)
        line_h = max(text_size(draw, line, font)[1] for line in lines)
        total_h = line_h * len(lines) + TITLE_LINE_GAP * (len(lines) - 1)
        last_lines, last_h = lines, total_h
        if total_h <= max_height:
            if len(lines) <= target_lines:
                return font, lines, line_h, total_h
            if fallback is None:
                fallback = (font, lines, line_h, total_h)
        size -= 2
    if fallback is not None:
        return fallback
    raise OverlayFitError(
        f"tiêu đề chiếm {last_h:.0f}px / {len(last_lines)} dòng, chỉ còn {max_height:.0f}px "
        f"ở cỡ chữ nhỏ nhất ({MIN_TITLE_SIZE}px)"
    )


def layout_items(
    draw: ImageDraw.ImageDraw,
    items: list[dict],
    font: ImageFont.FreeTypeFont,
    max_width: int,
) -> tuple[list[tuple[str, list[str]]], float, int]:
    """Xếp items ở một cỡ chữ: (prefix, dòng mô tả) + chiều cao đã dùng.

    @returns (items đã xếp, tổng chiều cao, chiều cao một dòng)
    """
    prepared: list[tuple[str, list[str]]] = []
    total_h = 0.0
    line_h = MIN_BODY_SIZE
    for idx, item in enumerate(items, start=1):
        prefix = f"{idx}.  {item['label']}: "
        prefix_w, line_h = text_size(draw, prefix, font)
        remain = max(max_width - prefix_w, 80)
        desc_lines = wrap_lines(draw, item["text"], font, remain)
        # Nếu prefix + dòng đầu vẫn quá ngang thì wrap lại toàn bộ phần mô tả.
        if text_size(draw, prefix + desc_lines[0], font)[0] > max_width:
            desc_lines = wrap_lines(draw, item["text"], font, max_width - 48)
        total_h += line_h * len(desc_lines) + ITEM_LINE_GAP * (len(desc_lines) - 1) + ITEM_ROW_GAP
        prepared.append((prefix, desc_lines))
    return prepared, total_h, line_h


def min_items_height(
    draw: ImageDraw.ImageDraw, items: list[dict], font_path: Path, max_width: int
) -> float:
    """Chiều cao danh sách ở cỡ chữ nhỏ nhất — dùng để chừa chỗ cho tiêu đề."""
    return layout_items(draw, items, load_font(font_path, MIN_BODY_SIZE), max_width)[1]


def fit_items(
    draw: ImageDraw.ImageDraw,
    items: list[dict],
    font_path: Path,
    max_width: int,
    max_height: int,
) -> tuple[ImageFont.FreeTypeFont, list[tuple[str, list[str]]], float, int]:
    """Co cỡ chữ body tới khi toàn bộ items vừa `max_height`.

    @returns (font, [(prefix, dòng mô tả)], tổng chiều cao đã dùng, chiều cao một dòng)
    @raises OverlayFitError khi cỡ chữ nhỏ nhất vẫn không đủ chỗ
    """
    size = 40
    prepared: list[tuple[str, list[str]]] = []
    total_h = 0.0
    line_h = MIN_BODY_SIZE
    while size >= MIN_BODY_SIZE:
        font = load_font(font_path, size)
        prepared, total_h, line_h = layout_items(draw, items, font, max_width)
        if total_h <= max_height:
            return font, prepared, total_h, line_h
        size -= 2
    raise OverlayFitError(
        f"{len(items)} items cần {total_h:.0f}px, chỉ còn {max_height:.0f}px "
        f"ở cỡ chữ nhỏ nhất ({MIN_BODY_SIZE}px)"
    )


def render_overlay(data: dict, fonts_dir: Path, out_path: Path, mode: str = "full") -> Path:
    cfg = load_config()
    width = int(cfg.video.width)
    height = int(cfg.video.height)
    sz_top = int(cfg.safe_zone.top)
    sz_bottom = int(cfg.safe_zone.bottom)
    sz_side = int(cfg.safe_zone.side)

    title_font_path = fonts_dir / cfg.paths.title_font
    body_font_path = fonts_dir / cfg.paths.body_font
    footer_font_path = fonts_dir / cfg.paths.footer_font

    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    panel = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    panel_draw = ImageDraw.Draw(panel)

    margin_x = sz_side
    panel_top = sz_top
    panel_bottom = height - sz_bottom
    radius = int(cfg.style.get("panel_radius", 28))
    fill = tuple(cfg.style.panel_rgba)  # type: ignore[arg-type]
    panel_draw.rounded_rectangle(
        [margin_x - 8, panel_top, width - margin_x + 8, panel_bottom],
        radius=radius,
        fill=fill,
    )
    panel = panel.filter(ImageFilter.GaussianBlur(1.2))
    img = Image.alpha_composite(img, panel)
    draw = ImageDraw.Draw(img)

    title_start = int(cfg.hook.title_size) if mode == "hook" else int(
        cfg.hook.get("full_title_size", 62)
    )
    footer_font = load_font(footer_font_path, FOOTER_SIZE)
    footer = data.get("footer", "")
    footer_y = panel_bottom - 64

    title_color = (*hex_to_rgb(cfg.style.title_color), 255)
    label_color = (*hex_to_rgb(cfg.style.item_label_color), 255)
    body_color = (*hex_to_rgb(cfg.style.item_text_color), 255)
    footer_color = (*hex_to_rgb(cfg.style.footer_color), 255)
    stroke = tuple(cfg.style.stroke_rgba)  # type: ignore[arg-type]
    stroke_w = int(cfg.style.stroke_width)

    max_text_width = width - 2 * margin_x - 24
    title_top = panel_top + 48
    items = data.get("items", [])
    if mode == "hook":
        # Chừa chỗ cho dòng "N ĐIỀU" ở giữa panel.
        title_max_h = max((panel_top + panel_bottom) // 2 - 20 - title_top, 120)
    else:
        # Chừa đúng chỗ danh sách cần ở cỡ chữ nhỏ nhất để items luôn còn đất.
        body_min_h = min_items_height(draw, items, body_font_path, max_text_width)
        title_max_h = max(footer_y - 24 - title_top - body_min_h - 20, 120)
    title_font, title_lines, title_line_h, _ = fit_title(
        draw, data["title"], title_font_path, max_text_width, title_max_h, title_start
    )

    y = title_top
    for line in title_lines:
        draw_text(
            draw, (width // 2, y), line, title_font, title_color,
            stroke_width=stroke_w, stroke_fill=stroke, anchor="mt",
        )
        y += title_line_h + TITLE_LINE_GAP

    if mode == "hook":
        # Big title only — 3s đầu đọc được khi tắt tiếng
        if footer:
            draw_text(
                draw, (width // 2, footer_y), footer, footer_font, footer_color,
                stroke_width=stroke_w, stroke_fill=stroke, anchor="mt",
            )
        n = len(data.get("items") or [])
        hint_font = load_font(footer_font_path, HINT_SIZE)
        draw_text(
            draw,
            (width // 2, (panel_top + panel_bottom) // 2 + 40),
            f"{n} ĐIỀU",
            hint_font,
            label_color,
            stroke_width=stroke_w,
            stroke_fill=stroke,
            anchor="mm",
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(out_path)
        return out_path

    y += 20
    usable_bottom = footer_y - 24
    remaining = usable_bottom - y
    if remaining <= 0:
        raise OverlayFitError(
            f"tiêu đề chiếm hết chỗ: không còn đất cho {len(items)} items"
        )
    body_font, prepared, items_h, line_h = fit_items(
        draw, items, body_font_path, max_text_width, remaining
    )

    # Chia đều khoảng trống còn lại giữa các item, có trần để không rời rạc.
    bonus = 0.0
    free = usable_bottom - y - items_h
    if len(prepared) > 1 and free > 0:
        bonus = min(free / len(prepared), MAX_ITEM_GAP_BONUS)

    row_cursor = y
    for prefix, desc_lines in prepared:
        prefix_w, _ = text_size(draw, prefix, body_font)
        x0 = margin_x + 12
        draw_text(
            draw, (x0, row_cursor), prefix, body_font, label_color,
            stroke_width=stroke_w, stroke_fill=stroke, anchor="lt",
        )
        first = desc_lines[0] if desc_lines else ""
        draw_text(
            draw, (x0 + prefix_w, row_cursor), first, body_font, body_color,
            stroke_width=stroke_w, stroke_fill=stroke, anchor="lt",
        )
        for extra in desc_lines[1:]:
            row_cursor += line_h + ITEM_LINE_GAP
            draw_text(
                draw, (x0 + 36, row_cursor), extra, body_font, body_color,
                stroke_width=stroke_w, stroke_fill=stroke, anchor="lt",
            )
        row_cursor += line_h + ITEM_ROW_GAP + bonus

    if footer:
        draw_text(
            draw, (width // 2, footer_y), footer, footer_font, footer_color,
            stroke_width=stroke_w, stroke_fill=stroke, anchor="mt",
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path)
    return out_path


def render_pair(data: dict, fonts_dir: Path, overlay_dir: Path) -> tuple[Path, Path]:
    clip_id = data["id"]
    hook = overlay_dir / f"{clip_id}_hook.png"
    full = overlay_dir / f"{clip_id}.png"
    render_overlay(data, fonts_dir, hook, mode="hook")
    render_overlay(data, fonts_dir, full, mode="full")
    return hook, full


def main() -> None:
    parser = argparse.ArgumentParser(description="Render quote overlay PNG")
    parser.add_argument("--json", required=True, help="Path to list JSON")
    parser.add_argument("--out", default="", help="Output PNG path (full list)")
    parser.add_argument("--mode", choices=("full", "hook", "both"), default="both")
    args = parser.parse_args()

    cfg = load_config()
    json_path = Path(args.json)
    data = load_clip(json_path) if json_path.exists() else validate_clip(
        json.loads(json_path.read_text(encoding="utf-8"))
    )
    for warning in content_warnings(data):
        print(f"Cảnh báo nội dung: {warning}")
    fonts = resolve_path(cfg.paths.fonts_dir)
    overlay_dir = resolve_path(cfg.paths.overlay_dir)
    try:
        if args.mode == "both":
            hook, full = render_pair(data, fonts, overlay_dir)
            print(hook)
            print(full)
            return
        out = Path(args.out) if args.out else overlay_dir / (
            f"{data['id']}_hook.png" if args.mode == "hook" else f"{data['id']}.png"
        )
        path = render_overlay(data, fonts, out, mode=args.mode)
        print(path)
    except OverlayFitError as exc:
        raise SystemExit(f"{json_path.name}: {exc}. Rút ngắn tiêu đề/bớt item rồi chạy lại.") from exc


if __name__ == "__main__":
    main()
