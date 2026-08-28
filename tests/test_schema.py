#!/usr/bin/env python3
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from schema import ClipError, default_caption, default_voice_script, validate_clip  # noqa: E402
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


if __name__ == "__main__":
    unittest.main()
