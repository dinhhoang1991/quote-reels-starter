#!/usr/bin/env python3
"""Overlay phải nằm trong safe zone: auto-fit, chia khoảng trống, và báo lỗi khi tràn."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PIL import Image, ImageDraw  # noqa: E402

import render_overlay as overlay_module  # noqa: E402
from config import Cfg, load_config  # noqa: E402
from render_overlay import (  # noqa: E402
    OverlayFitError,
    content_bottom_limit,
    fit_title,
    render_overlay,
)
from schema import load_clip, validate_clip  # noqa: E402
from subtitles import overlay_limit  # noqa: E402

FONTS = ROOT / "assets" / "fonts"
SAMPLES = ROOT / "data" / "samples"


def ink_rows(path: Path) -> list[int]:
    """Các dòng có pixel chữ. Panel là đen thuần nên bỏ qua được."""
    image = Image.open(path).convert("RGB")
    pixels = image.load()
    rows = []
    for y in range(image.height):
        for x in range(0, image.width, 8):
            r, g, b = pixels[x, y]
            if r + g + b > 30:
                rows.append(y)
                break
    return rows


class OverlayTest(unittest.TestCase):
    def setUp(self):
        self.cfg = load_config()
        self.panel_bottom = int(self.cfg.video.height) - int(self.cfg.safe_zone.bottom)
        self.footer_y = self.panel_bottom - 64
        self.height = int(self.cfg.video.height)
        self.subtitle_style = (self.cfg.get("subtitles", {}) or {}).as_dict()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name)

    def render(self, data: dict, mode: str, footer: bool = True, cfg=None) -> list[int]:
        payload = dict(data)
        if not footer:
            payload["footer"] = ""
        out = self.out / f"{data['id']}_{mode}_{int(footer)}_{cfg is not None}.png"
        if cfg is None:
            render_overlay(payload, FONTS, out, mode=mode)
        else:
            with mock.patch.object(overlay_module, "load_config", return_value=cfg):
                render_overlay(payload, FONTS, out, mode=mode)
        return ink_rows(out)

    def long_items(self, count: int, clip_id: str = "dai") -> dict:
        """Clip `count` item chữ dài — đúng khoảng 8-10 item mà content_warnings khuyến nghị."""
        return validate_clip(
            {
                "id": clip_id,
                "title": "TIÊU ĐỀ KIỂM TRA",
                "items": [
                    {
                        "label": f"Nhãn siêu dài cho mục số {i}",
                        "text": "Phần giải thích khá dài dòng cho mục này để xem chữ có bị "
                        "tràn ra ngoài khung an toàn hay không",
                    }
                    for i in range(1, count + 1)
                ],
                "footer": "BẠN NGHĨ SAO?",
            }
        )

    def test_samples_stay_inside_safe_zone(self):
        for name in ("clip_001", "clip_002"):
            data = load_clip(SAMPLES / f"{name}.json")
            for mode in ("hook", "full"):
                rows = self.render(data, mode)
                self.assertGreaterEqual(rows[0], int(self.cfg.safe_zone.top), f"{name}/{mode} lên quá cao")
                self.assertLessEqual(rows[-1], self.panel_bottom, f"{name}/{mode} tràn khỏi panel")
                # Không có footer thì phần chữ phải kết thúc trước vạch footer.
                body_rows = self.render(data, mode, footer=False)
                self.assertLessEqual(
                    body_rows[-1], self.footer_y - 12, f"{name}/{mode} đè lên footer"
                )

    def test_long_title_shrinks_instead_of_overflowing(self):
        draw = ImageDraw.Draw(Image.new("RGBA", (1080, 1920)))
        font, lines, line_h, total_h = fit_title(
            draw,
            "TIÊU ĐỀ RẤT DÀI DÒNG NÀY " * 4,
            FONTS / self.cfg.paths.title_font,
            912,
            300,
            62,
        )
        self.assertLess(font.size, 62)
        self.assertLessEqual(total_h, 300)
        self.assertGreaterEqual(line_h, 1)
        self.assertTrue(lines)

    def test_title_keeps_the_number_of_lines_the_author_wrote(self):
        draw = ImageDraw.Draw(Image.new("RGBA", (1080, 1920)))
        font, lines, _, _ = fit_title(
            draw,
            "PHÂN LOẠI TÀI SẢN\nTHEO THU NHẬP MỖI THÁNG",
            FONTS / self.cfg.paths.title_font,
            912,
            620,
            78,
        )
        self.assertEqual(len(lines), 2)
        self.assertLess(font.size, 78)

    def test_items_use_the_free_space_instead_of_stacking_on_top(self):
        data = validate_clip(
            {
                "id": "spread",
                "title": "TIÊU ĐỀ NGẮN",
                "items": [{"label": f"Nhãn {i}", "text": "Ngắn"} for i in range(1, 9)],
                "footer": "BẠN NGHĨ SAO?",
            }
        )
        rows = self.render(data, "full", footer=False)
        self.assertGreater(rows[-1], 900)
        self.assertLessEqual(rows[-1], self.footer_y - 12)

    def test_unfittable_items_fail_loud(self):
        data = validate_clip(
            {
                "id": "qua_dai",
                "title": "TIÊU ĐỀ",
                "items": [
                    {
                        "label": f"Nhãn siêu dài cho mục số {i}",
                        "text": "Phần giải thích rất dài dòng cho mục này để kiểm tra "
                        "xem chữ có bị tràn ra ngoài khung an toàn hay không khi có "
                        "đủ mười hai mục trong danh sách",
                    }
                    for i in range(1, 13)
                ],
                "footer": "BẠN NGHĨ SAO?",
            }
        )
        with self.assertRaises(OverlayFitError):
            render_overlay(data, FONTS, self.out / "qua_dai.png", mode="full")

    def test_twelve_line_title_no_longer_overlaps_the_footer(self):
        data = validate_clip(
            {
                "id": "title_dai",
                "title": "\n".join(f"DÒNG TIÊU ĐỀ SỐ {i} RẤT DÀI" for i in range(1, 13)),
                "items": [{"label": f"Nhãn {i}", "text": "Ngắn"} for i in range(1, 11)],
                "footer": "BẠN NGHĨ SAO?",
            }
        )
        rows = self.render(data, "full", footer=False)
        self.assertLessEqual(rows[-1], self.footer_y - 12)

    def test_items_stop_above_the_subtitle_band(self):
        """8-9 item chữ dài trước đây chạm tới 1436-1450px, đè lên dải phụ đề 1386-1450px."""
        limit = overlay_limit(self.subtitle_style, self.height)
        self.assertEqual(limit, 1370)
        for count in (8, 9):
            with self.subTest(items=count):
                data = self.long_items(count, clip_id=f"dai_{count}")
                rows = self.render(data, "full", footer=False)
                self.assertLessEqual(rows[-1], limit, f"{count} item đè lên dải phụ đề")

    def test_overlay_reclaims_the_subtitle_band_when_subtitles_are_off(self):
        off = Cfg({**self.cfg.as_dict(), "subtitles": {**self.subtitle_style, "enabled": False}})
        data = self.long_items(9, clip_id="tat_phu_de")
        rows_on = self.render(data, "full", footer=False)
        rows_off = self.render(data, "full", footer=False, cfg=off)
        self.assertLessEqual(rows_on[-1], overlay_limit(self.subtitle_style, self.height))
        self.assertGreater(rows_off[-1], overlay_limit(self.subtitle_style, self.height))
        self.assertLessEqual(rows_off[-1], self.footer_y - 12)

    def test_content_bottom_limit_follows_the_subtitles_switch(self):
        limit_on = content_bottom_limit(self.footer_y, self.height, self.cfg)
        off = Cfg({**self.cfg.as_dict(), "subtitles": {**self.subtitle_style, "enabled": False}})
        self.assertEqual(content_bottom_limit(self.footer_y, self.height, off), self.footer_y - 24)
        self.assertEqual(limit_on, overlay_limit(self.subtitle_style, self.height))


if __name__ == "__main__":
    unittest.main()
