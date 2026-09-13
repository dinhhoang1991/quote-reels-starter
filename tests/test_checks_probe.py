#!/usr/bin/env python3
"""Probe bằng ffmpeg: parser output `ffmpeg -i` và đo độ dài stream audio."""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from checks import (  # noqa: E402
    audio_stream_seconds,
    ffmpeg_gaps,
    ffmpeg_stream_info,
    parse_capabilities,
    parse_stream_info,
)

# Output thật của `ffmpeg -i` cho file do pipeline sinh ra
CAPTURED = """Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'assets/out/clip_001.mp4':
  Metadata:
    major_brand     : isom
  Duration: 00:00:21.87, start: 0.000000, bitrate: 3278 kb/s
  Stream #0:0[0x1](und): Video: h264 (High) (avc1 / 0x31637661), yuv420p(progressive), 1080x1920 [SAR 1:1 DAR 9:16], 3141 kb/s, 30 fps, 30 tbr, 15360 tbn (default)
  Stream #0:1[0x2](und): Audio: aac (LC) (mp4a / 0x6134706D), 48000 Hz, stereo, fltp, 127 kb/s (default)
"""
CAPTURED_MONO = """  Duration: 00:00:04.00, start: 0.000000, bitrate: 64 kb/s
  Stream #0:0: Audio: pcm_s16le ([1][0][0][0] / 0x0001), 24000 Hz, mono, s16, 384 kb/s
"""
CAPTURED_NO_STREAMS = "ffmpeg version 7.0.2\n"


class ParseStreamInfoTest(unittest.TestCase):
    def test_doc_video_audio_va_duration(self):
        info = parse_stream_info(CAPTURED)
        self.assertEqual(info["duration"], 21.87)
        self.assertEqual(info["video"], {"width": 1080, "height": 1920, "fps": 30.0})
        self.assertEqual(info["audio"]["sample_rate"], 48000)
        self.assertEqual(info["audio"]["layout"], "stereo")
        self.assertEqual(info["audio"]["channels"], 2)

    def test_audio_mono_24k(self):
        info = parse_stream_info(CAPTURED_MONO)
        self.assertEqual(info["duration"], 4.0)
        self.assertEqual(info["audio"]["sample_rate"], 24000)
        self.assertEqual(info["audio"]["channels"], 1)
        self.assertNotIn("video", info)

    def test_thieu_thong_tin_thi_bo_khoa(self):
        self.assertEqual(parse_stream_info(CAPTURED_NO_STREAMS), {})

    def test_khong_nham_so_trong_ten_codec(self):
        """'avc1 / 0x31637661' chứa số, không được nhận thành kích thước video."""
        info = parse_stream_info(CAPTURED)
        self.assertEqual((info["video"]["width"], info["video"]["height"]), (1080, 1920))


@unittest.skipIf(shutil.which("ffmpeg") is None, "không có ffmpeg")
class RealProbeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.wav = Path(self.tmp.name) / "two-seconds.wav"
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=300:duration=2",
             "-ac", "1", "-ar", "24000", str(self.wav)],
            check=True,
        )

    def test_do_dung_do_dai_audio_cua_wav(self):
        self.assertAlmostEqual(audio_stream_seconds(self.wav, 24000, 1), 2.0, delta=0.02)

    def test_do_duoc_audio_trong_mp4_sau_khi_render(self):
        mp4 = Path(self.tmp.name) / "clip.mp4"
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=180x320:d=2",
             "-f", "lavfi", "-i", "sine=frequency=300:duration=2", "-c:v", "libx264",
             "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000", "-ac", "2", "-shortest", str(mp4)],
            check=True,
        )
        info = ffmpeg_stream_info(mp4)
        self.assertEqual(info["video"]["width"], 180)
        self.assertEqual(info["audio"]["sample_rate"], 48000)
        self.assertAlmostEqual(audio_stream_seconds(mp4), 2.0, delta=0.05)

    def test_file_hong_thi_tra_0(self):
        broken = Path(self.tmp.name) / "broken.mp4"
        broken.write_bytes(b"khong phai video")
        self.assertEqual(audio_stream_seconds(broken), 0.0)


# Output thật (rút gọn) của `ffmpeg -encoders` và `-filters`
CAPTURED_ENCODERS = """Encoders:
 V..... = Video
 A..... = Audio
 ------
 V....D libx264              libx264 H.264 / AVC / MPEG-4 AVC / MPEG-4 part 10 (codec h264)
 A....D aac                  AAC (Advanced Audio Coding)
 A....D libmp3lame           libmp3lame MP3 (MPEG audio layer 3)
"""
CAPTURED_FILTERS = """Filters:
  T.. = Timeline support
  .S. = Slice threading
  ..C = Command support
  A = Audio input/output
  V = Video input/output
  N = Dynamic number and/or type of input/output
  | = Source or sink filter
 ... zoompan           V->V       Apply Zoom & Pan effect.
 ..C sidechaincompress AA->A      Sidechain compressor.
 ... loudnorm          A->A       EBU R128 loudness normalization
 ... gradients         |->V       Draw a gradients.
 ... anoisesrc         |->A       Generate a noise audio signal.
 ... subtitles         V->V       Render text subtitles onto input video using libass.
"""


class ParseCapabilitiesTest(unittest.TestCase):
    def test_boc_encoder(self):
        names = parse_capabilities(CAPTURED_ENCODERS)
        self.assertIn("libx264", names)
        self.assertIn("aac", names)
        self.assertNotIn("video", names)

    def test_boc_filter_va_bo_dong_chu_giai(self):
        names = parse_capabilities(CAPTURED_FILTERS)
        self.assertIn("zoompan", names)
        self.assertIn("sidechaincompress", names)
        self.assertIn("loudnorm", names)
        self.assertIn("subtitles", names)
        # các dòng chú giải ("T.. = Timeline support") không được thành tên filter
        self.assertNotIn("=", names)
        self.assertNotIn("timeline", names)

    def test_output_rong(self):
        self.assertEqual(parse_capabilities(""), set())


@unittest.skipIf(shutil.which("ffmpeg") is None, "không có ffmpeg")
class RealCapabilitiesTest(unittest.TestCase):
    def test_ffmpeg_that_co_du_codec_va_filter(self):
        missing, optional = ffmpeg_gaps()
        self.assertEqual(missing, [], f"ffmpeg thiếu {missing}")
        # subtitles chỉ cần khi bật phụ đề nên không tính là bắt buộc
        self.assertIsInstance(optional, list)


if __name__ == "__main__":
    unittest.main()
