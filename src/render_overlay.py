#!/usr/bin/env python3
"""Render 1080x1920 transparent PNG overlays (hook + full) from list JSON.

Respects config.yaml colors, fonts, and Facebook Reels safe zones.
Long item text wraps instead of overflowing.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from config import load_config, resolve_path, root
from schema import load_clip, validate_clip

ROOT = root()


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


def _prepare_items(
    draw: ImageDraw.ImageDraw,
    items: list[dict],
    font_path: Path,
    max_width: int,
    max_block_h: int,
) -> tuple[ImageFont.FreeTypeFont, list[tuple[str, list[str]]]]:
    size = 40
    prepared: list[tuple[str, list[str]]] = []
    font = load_font(font_path, size)
    while size >= 26:
        font = load_font(font_path, size)
        prepared = []
        total_h = 0
        fits = True
        for idx, item in enumerate(items, start=1):
            prefix = f"{idx}.  {item['label']}: "
            prefix_w, line_h = text_size(draw, prefix, font)
            remain = max(max_width - prefix_w, 80)
            desc_lines = wrap_lines(draw, item["text"], font, remain)
            if not desc_lines:
                desc_lines = [item["text"]]
            # If first desc line + prefix still too wide, wrap the whole as two blocks
            full = prefix + desc_lines[0]
            w, _ = text_size(draw, full, font)
            if w > max_width:
                desc_lines = wrap_lines(draw, item["text"], font, max_width - 48)
            block_h = line_h * max(len(desc_lines), 1) + 10
            total_h += block_h
            prepared.append((prefix, desc_lines))
        if total_h <= max_block_h and fits:
            return font, prepared
        size -= 2
    return font, prepared


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

    title_size = int(cfg.hook.title_size) if mode == "hook" else 62
    title_font = load_font(title_font_path, title_size)
    footer_font = load_font(footer_font_path, 36)

    title_color = (*hex_to_rgb(cfg.style.title_color), 255)
    label_color = (*hex_to_rgb(cfg.style.item_label_color), 255)
    body_color = (*hex_to_rgb(cfg.style.item_text_color), 255)
    footer_color = (*hex_to_rgb(cfg.style.footer_color), 255)
    stroke = tuple(cfg.style.stroke_rgba)  # type: ignore[arg-type]
    stroke_w = int(cfg.style.stroke_width)

    max_text_width = width - 2 * margin_x - 24
    title_lines = wrap_lines(draw, data["title"], title_font, max_text_width)
    y = panel_top + 48
    for line in title_lines:
        draw_text(
            draw, (width // 2, y), line, title_font, title_color,
            stroke_width=stroke_w, stroke_fill=stroke, anchor="mt",
        )
        _, h = text_size(draw, line, title_font)
        y += h + 8

    footer = data.get("footer", "")
    footer_y = panel_bottom - 64

    if mode == "hook":
        # Big title only — 3s đầu đọc được khi tắt tiếng
        if footer:
            draw_text(
                draw, (width // 2, footer_y), footer, footer_font, footer_color,
                stroke_width=stroke_w, stroke_fill=stroke, anchor="mt",
            )
        n = len(data.get("items") or [])
        hint_font = load_font(footer_font_path, 32)
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
    items = data.get("items", [])
    usable_bottom = footer_y - 24
    remaining = max(usable_bottom - y, 200)
    body_font, prepared = _prepare_items(draw, items, body_font_path, max_text_width, remaining)

    row_cursor = y
    for prefix, desc_lines in prepared:
        prefix_w, line_h = text_size(draw, prefix, body_font)
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
            row_cursor += line_h + 2
            draw_text(
                draw, (x0 + 36, row_cursor), extra, body_font, body_color,
                stroke_width=stroke_w, stroke_fill=stroke, anchor="lt",
            )
        row_cursor += line_h + 14

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
        json.loads(json_path.read_text())
    )
    fonts = resolve_path(cfg.paths.fonts_dir)
    overlay_dir = resolve_path(cfg.paths.overlay_dir)
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


if __name__ == "__main__":
    main()
