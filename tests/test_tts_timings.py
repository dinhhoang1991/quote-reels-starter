#!/usr/bin/env python3
"""TTS có timing: sidecar WordBoundary, và make_video nhúng phụ đề khi có timing."""

import asyncio
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import make_video  # noqa: E402
import tts  # noqa: E402
from config import Cfg  # noqa: E402


class FakeCommunicate:
    """Giả edge_tts.Communicate: phát vài chunk audio + WordBoundary."""

    def __init__(self, text, voice=None, rate=None, boundary=None):
        self.text = text
        self.boundary = boundary

    async def stream(self):
        yield {"type": "audio", "data": b"ID3fake-mp3-bytes"}
        yield {"type": "WordBoundary", "text": "Xin", "offset": 1_000_000, "duration": 3_000_000}
        yield {"type": "WordBoundary", "text": "chào", "offset": 4_000_000, "duration": 2_000_000}
        yield {"type": "audio", "data": b"more-bytes"}


def fake_edge_tts() -> types.ModuleType:
    module = types.ModuleType("edge_tts")
    module.Communicate = FakeCommunicate
    return module


class TimingsFileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.voice = Path(self.tmp.name) / "clip.abc123.mp3"

    def test_duong_dan_sidecar(self):
        self.assertEqual(tts.timings_path(self.voice).name, "clip.abc123.mp3.words.json")

    def test_round_trip(self):
        words = [{"text": "Xin", "offset": 1_000_000, "duration": 3_000_000}]
        path = tts.save_timings(self.voice, words)
        self.assertEqual(path, tts.timings_path(self.voice))
        self.assertEqual(tts.load_timings(self.voice), words)

    def test_chua_co_sidecar(self):
        self.assertEqual(tts.load_timings(self.voice), [])

    def test_sidecar_hong(self):
        tts.timings_path(self.voice).write_text("{hong", encoding="utf-8")
        self.assertEqual(tts.load_timings(self.voice), [])

    def test_sidecar_sai_cau_truc(self):
        tts.timings_path(self.voice).write_text(json.dumps(["khong-phai-dict"]), encoding="utf-8")
        self.assertEqual(tts.load_timings(self.voice), [])


class SynthWithTimingsTest(unittest.TestCase):
    def test_ghi_mp3_va_timing(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "voice.mp3"
            with mock.patch.dict(sys.modules, {"edge_tts": fake_edge_tts()}):
                path, words = asyncio.run(
                    tts.synth_with_timings("Xin chào", out, "vi-VN-NamMinhNeural", "-8%")
                )
            self.assertEqual(path, out)
            self.assertEqual(out.read_bytes(), b"ID3fake-mp3-bytesmore-bytes")
            self.assertEqual([word["text"] for word in words], ["Xin", "chào"])
            self.assertEqual(tts.load_timings(out), words)

    def test_dung_boundary_word(self):
        seen = {}

        class Capture(FakeCommunicate):
            def __init__(self, text, voice=None, rate=None, boundary=None):
                super().__init__(text, voice, rate, boundary)
                seen["boundary"] = boundary

        module = fake_edge_tts()
        module.Communicate = Capture
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(sys.modules, {"edge_tts": module}),
        ):
            asyncio.run(tts.synth_with_timings("a", Path(tmp) / "v.mp3", "voice", "+0%"))
        self.assertEqual(seen["boundary"], "WordBoundary")


class BuildSubtitlesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = Cfg({
            "video": {"width": 1080, "height": 1920},
            "audio": {"head_seconds": 0.6},
            "paths": {"overlay_dir": self.tmp.name},
            "subtitles": {"enabled": True, "words_per_cue": 3, "margin_v": 470},
        })
        self.data = {"id": "clip_x"}
        self.voice = Path(self.tmp.name) / "clip_x.mp3"
        self.voice.write_bytes(b"mp3")

    def test_bat_phu_de_thi_sinh_ass(self):
        tts.save_timings(self.voice, [
            {"text": "Xin", "offset": 0, "duration": 200 * 10_000},
            {"text": "chào", "offset": 250 * 10_000, "duration": 200 * 10_000},
        ])
        path = make_video.build_subtitles(self.data, self.voice, self.cfg)
        self.assertIsNotNone(path)
        content = path.read_text(encoding="utf-8")
        self.assertIn("XIN CHÀO", content)
        # cue đầu phải dịch theo head 0.6s
        self.assertIn("0:00:00.60", content)

    def test_tat_thi_khong_sinh(self):
        cfg = Cfg({**self.cfg.as_dict(), "subtitles": {"enabled": False}})
        self.assertIsNone(make_video.build_subtitles(self.data, self.voice, cfg))

    def test_khong_co_timing_thi_bo_qua(self):
        self.assertIsNone(make_video.build_subtitles(self.data, self.voice, self.cfg))

    def test_khong_co_timing_va_khong_bat_buoc_thi_sinh_voi_1_cue(self):
        raw = self.cfg.as_dict()
        raw["subtitles"] = {"enabled": True, "require_timings": False}
        self.assertIsNone(make_video.build_subtitles(self.data, self.voice, Cfg(raw)))

    def test_voice_none_thi_bo_qua(self):
        self.assertIsNone(make_video.build_subtitles(self.data, None, self.cfg))

    def test_subtitles_enabled_doc_dung_config(self):
        self.assertTrue(make_video.subtitles_enabled(self.cfg))
        self.assertFalse(make_video.subtitles_enabled(Cfg({"subtitles": {}})))


if __name__ == "__main__":
    unittest.main()
