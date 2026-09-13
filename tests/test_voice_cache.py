#!/usr/bin/env python3
"""Voice cache: khoá theo hash(lời thoại, giọng, rate) nên không dùng lại bản đọc cũ."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from config import Cfg, load_config  # noqa: E402
from make_video import ensure_voice  # noqa: E402
from schema import validate_clip  # noqa: E402
from tts import voice_cache_key, voice_path  # noqa: E402

CLIP = {"id": "clip_x", "title": "TIÊU ĐỀ", "items": [{"label": "Nhãn", "text": "Vài chữ"}]}


def with_config(**overrides) -> Cfg:
    raw = load_config().as_dict()
    for section, values in overrides.items():
        raw[section].update(values)
    return Cfg(raw)


class VoiceCacheKeyTest(unittest.TestCase):
    def test_key_on_dinh_voi_cung_noi_dung(self):
        cfg = load_config()
        data = validate_clip(dict(CLIP))
        self.assertEqual(voice_cache_key(data, cfg), voice_cache_key(dict(data), cfg))

    def test_doi_loi_thoai_la_doi_file(self):
        cfg = load_config()
        base = validate_clip(dict(CLIP))
        other = validate_clip({**CLIP, "voice_script": "Lời thoại khác hẳn."})
        self.assertNotEqual(voice_path(base, cfg).name, voice_path(other, cfg).name)

    def test_doi_giong_hoac_rate_la_doi_file(self):
        data = validate_clip(dict(CLIP))
        base = voice_path(data, load_config())
        self.assertNotEqual(base.name, voice_path(data, with_config(audio={"voice_name": "khac"})).name)
        self.assertNotEqual(base.name, voice_path(data, with_config(audio={"voice_rate": "+10%"})).name)

    def test_ten_file_nam_trong_voice_dir_va_kem_hash(self):
        cfg = load_config()
        data = validate_clip(dict(CLIP))
        path = voice_path(data, cfg)
        self.assertEqual(path.parent.name, "voice")
        self.assertEqual(path.name, f"clip_x.{voice_cache_key(data, cfg)}.mp3")


class EnsureVoiceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = with_config(paths={"voice_dir": self.tmp.name})
        self.data = validate_clip(dict(CLIP))

    def test_dung_lai_cache_khi_con_dung(self):
        dest = voice_path(self.data, self.cfg)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes("mp3 cũ".encode())
        with mock.patch("make_video.load_config", return_value=self.cfg), mock.patch("tts.synth_with_timings") as synth:
                self.assertEqual(ensure_voice(self.data, None), dest)
        synth.assert_not_called()

    def test_goi_tts_khi_chua_co_cache(self):
        expected = voice_path(self.data, self.cfg)
        with mock.patch("make_video.load_config", return_value=self.cfg), mock.patch("tts.synth_with_timings") as synth:
                path = ensure_voice(self.data, None)
        self.assertEqual(path, expected)
        synth.assert_called_once_with(
            self.data["voice_script"], expected,
            self.cfg.audio.voice_name, self.cfg.audio.voice_rate,
        )

    def test_doi_loi_thoai_thi_bo_qua_cache_cu(self):
        old = voice_path(self.data, self.cfg)
        old.parent.mkdir(parents=True, exist_ok=True)
        old.write_bytes("mp3 cũ".encode())
        changed = validate_clip({**CLIP, "voice_script": "Lời thoại mới hoàn toàn."})
        with mock.patch("make_video.load_config", return_value=self.cfg), mock.patch("tts.synth_with_timings") as synth:
                path = ensure_voice(changed, None)
        self.assertNotEqual(path, old)
        synth.assert_called_once()

    def test_voice_truyen_tay_thi_khong_dung_cache(self):
        manual = Path(self.tmp.name) / "vbee.mp3"
        manual.write_bytes(b"mp3")
        with mock.patch("make_video.load_config", return_value=self.cfg), mock.patch("tts.synth_with_timings") as synth:
                self.assertEqual(ensure_voice(self.data, manual), manual)
        synth.assert_not_called()

    def test_voice_truyen_tay_khong_ton_tai_thi_bao_loi(self):
        with mock.patch("make_video.load_config", return_value=self.cfg), self.assertRaises(SystemExit):
                ensure_voice(self.data, Path(self.tmp.name) / "khong-co.mp3")


if __name__ == "__main__":
    unittest.main()
