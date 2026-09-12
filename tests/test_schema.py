#!/usr/bin/env python3
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from schema import (  # noqa: E402
    ClipError,
    content_warnings,
    default_caption,
    default_voice_script,
    validate_clip,
)
from checks import clamp_duration  # noqa: E402
from config import load_config  # noqa: E402


SAMPLE = {
    "id": "clip_001",
    "title": "TIÊU ĐỀ\nHAI DÒNG",
    "items": [{"label": "A", "text": "Một"}, {"label": "B", "text": "Hai"}],
    "footer": "BẠN THẾ NÀO?",
}


class SchemaTest(unittest.TestCase):
    def test_valid(self):
        data = validate_clip(dict(SAMPLE))
        self.assertTrue(data["voice_script"])
        self.assertTrue(data["caption"])

    def test_missing_title(self):
        bad = dict(SAMPLE)
        bad.pop("title")
        with self.assertRaises(ClipError):
            validate_clip(bad)

    def test_bad_id(self):
        bad = dict(SAMPLE)
        bad["id"] = "../x"
        with self.assertRaises(ClipError):
            validate_clip(bad)

    def test_defaults_read_naturally(self):
        script = default_voice_script(SAMPLE)
        self.assertIn("TIÊU ĐỀ HAI DÒNG", script)
        cap = default_caption(SAMPLE)
        self.assertIn("1. A: Một", cap)

    def test_config_loads(self):
        cfg = load_config()
        self.assertEqual(int(cfg.video.width), 1080)
        self.assertEqual(int(cfg.audio.sample_rate), 48000)

    def test_clamp(self):
        self.assertEqual(clamp_duration(1.0), 3.0)
        self.assertEqual(clamp_duration(120.0), 90.0)
        self.assertEqual(clamp_duration(18.0), 18.0)


class ContentWarningTest(unittest.TestCase):
    def test_clean_clip_has_no_warning(self):
        data = validate_clip(
            {
                "id": "clean",
                "title": "TIÊU ĐỀ NGẮN",
                "items": [{"label": f"Nhãn {i}", "text": "Vài chữ"} for i in range(8)],
            }
        )
        self.assertEqual(content_warnings(data), [])

    def test_flags_title_items_and_text_over_the_prompt_rules(self):
        data = validate_clip(
            {
                "id": "long",
                "title": "MỘT HAI BA BỐN NĂM SÁU BẢY TÁM CHÍN MƯỜI",
                "items": [
                    {"label": "Nhãn ngắn", "text": "một hai ba bốn năm sáu bảy tám chín"},
                    {"label": "Nhãn ngắn", "text": "ngắn"},
                ],
            }
        )
        warnings = content_warnings(data)
        self.assertTrue(any("title" in w for w in warnings))
        self.assertTrue(any("items" in w for w in warnings))
        self.assertTrue(any("text" in w for w in warnings))

    def test_flags_long_label_and_two_line_title(self):
        data = validate_clip(
            {
                "id": "label",
                "title": "DÒNG MỘT\nDÒNG HAI",
                "items": [
                    {"label": "Nhãn này dài hơn sáu chữ nhiều", "text": "Ngắn"},
                    *[{"label": "Nhãn", "text": "Ngắn"} for _ in range(7)],
                ],
            }
        )
        warnings = content_warnings(data)
        self.assertTrue(any("label" in w for w in warnings))
        self.assertFalse(any("dòng" in w for w in warnings))


if __name__ == "__main__":
    unittest.main()
