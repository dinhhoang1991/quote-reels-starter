#!/usr/bin/env python3
"""Render a 1080x1920 transparent PNG overlay from a list JSON."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
WIDTH, HEIGHT = 1080, 1920


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def load_font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(path), size=size)


def text_size(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> tuple[int, int]:
    box = draw.textbbox((0, 0), text, font=font, stroke_width=0)
    return box[2] - box[0], box[3] - box[1]


def wrap_lines(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    if "\n" in text:
        raw = [part.strip() for part in text.split("\n") if part.strip()]
    else:
        raw = [text]
    lines: list[str] = []
    for part in raw:
        words = part.split()
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


def render_overlay(data: dict, fonts_dir: Path, out_path: Path) -> Path:
    title_font_path = fonts_dir / "BeVietnamPro-Black.ttf"
    body_font_path = fonts_dir / "BeVietnamPro-ExtraBold.ttf"
    footer_font_path = fonts_dir / "BeVietnamPro-SemiBold.ttf"

    img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    panel = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    panel_draw = ImageDraw.Draw(panel)

    margin_x = 70
    panel_top = 210
    panel_bottom = 1680
    panel_draw.rounded_rectangle(
        [margin_x - 20, panel_top, WIDTH - margin_x + 20, panel_bottom],
        radius=28,
        fill=(0, 0, 0, 150),
    )
    panel = panel.filter(ImageFilter.GaussianBlur(1.2))
    img = Image.alpha_composite(img, panel)
    draw = ImageDraw.Draw(img)

    title_font = load_font(title_font_path, 62)
    number_font = load_font(body_font_path, 40)
    label_font = load_font(body_font_path, 40)
    text_font = load_font(body_font_path, 40)
    footer_font = load_font(footer_font_path, 40)

    title_color = (*hex_to_rgb("#FF2A2A"), 255)
    label_color = (*hex_to_rgb("#FFE566"), 255)
    body_color = (*hex_to_rgb("#FFF4B0"), 255)
    footer_color = (*hex_to_rgb("#4CA3FF"), 255)

    max_text_width = WIDTH - 2 * margin_x - 20
    title_lines = wrap_lines(draw, data["title"], title_font, max_text_width)
    y = panel_top + 50
    for line in title_lines:
        draw_text(draw, (WIDTH // 2, y), line, title_font, title_color, anchor="mt")
        _, h = text_size(draw, line, title_font)
        y += h + 10

    y += 28
    items = data.get("items", [])
    usable_bottom = panel_bottom - 140
    remaining = max(usable_bottom - y, 200)
    row_h = remaining / max(len(items), 1)

    prepared = []
    size = 40
    max_w = 0
    while size >= 30:
        font = load_font(body_font_path, size)
        prepared = []
        max_w = 0
        fits = True
        for idx, item in enumerate(items, start=1):
            number = f"{idx}."
            label = item["label"].rstrip(":")
            desc = item["text"]
            prefix = f"{number}  {label}: "
            line = prefix + desc
            w, _ = text_size(draw, line, font)
            if w > max_text_width:
                fits = False
                break
            max_w = max(max_w, w)
            prepared.append((prefix, desc))
        if fits:
            break
        size -= 2

    left_x = (WIDTH - max_w) // 2
    body_used = load_font(body_font_path, size)
    for idx, (prefix, desc) in enumerate(prepared):
        row_y = int(y + row_h * idx + 8)
        prefix_w, _ = text_size(draw, prefix, body_used)
        draw_text(draw, (left_x, row_y), prefix, body_used, label_color, anchor="lt")
        draw_text(draw, (left_x + prefix_w, row_y), desc, body_used, body_color, anchor="lt")

    footer = data.get("footer", "")
    if footer:
        draw_text(draw, (WIDTH // 2, panel_bottom - 70), footer, footer_font, footer_color, anchor="mt")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path)
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Render quote overlay PNG")
    parser.add_argument("--json", required=True, help="Path to list JSON")
    parser.add_argument("--out", default="", help="Output PNG path")
    args = parser.parse_args()

    json_path = Path(args.json)
    data = json.loads(json_path.read_text(encoding="utf-8"))
    out = Path(args.out) if args.out else ROOT / "assets" / "overlays" / f"{data.get('id', json_path.stem)}.png"
    path = render_overlay(data, ROOT / "assets" / "fonts", out)
    print(path)


if __name__ == "__main__":
    main()
