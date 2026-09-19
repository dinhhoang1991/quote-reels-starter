#!/usr/bin/env python3
"""Filter graph cho make_video: audio phủ hết clip và Ken Burns trải hết clip."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from config import Cfg, load_config  # noqa: E402
from make_video import (  # noqa: E402
    audio_filter,
    background_filter,
    ken_burns_options,
    ken_burns_plan,
    ken_burns_zoom_at,
    segmented_background,
)


def cfg_with(ken_burns) -> Cfg:
    return Cfg(
        {
            "video": {"ken_burns": ken_burns},
            "audio": {
                "sample_rate": 48000,
                "music_volume": 0.12,
                "head_seconds": 0.6,
                "ducking": {"threshold": 0.06, "ratio": 7, "attack": 40, "release": 400},
            },
        }
    )


class AudioFilterTest(unittest.TestCase):
    def test_voice_is_padded_to_full_duration(self):
        """Thiếu apad thì amix=duration=first cắt audio theo voice và fade-out chết."""
        filt = audio_filter(load_config(), 43.34, 42.84)
        self.assertIn("adelay=600|600,apad=whole_dur=43.340", filt)
        self.assertIn("amix=inputs=2:duration=first", filt)

    def test_fade_out_starts_before_the_stream_ends(self):
        duration = 21.86
        fade_out_start = max(duration - 0.5, 0.2)
        filt = audio_filter(load_config(), duration, fade_out_start)
        self.assertIn(f"afade=t=out:st={fade_out_start:.2f}:d=0.45", filt)
        self.assertLess(fade_out_start, duration)

    def test_music_is_ducked_by_voice(self):
        filt = audio_filter(load_config(), 20.0, 19.5)
        self.assertIn("sidechaincompress=threshold=0.06:ratio=7", filt)
        self.assertIn("volume=0.12", filt)


class KenBurnsTest(unittest.TestCase):
    def test_zoom_reaches_zoom_end_on_the_last_frame(self):
        plan = ken_burns_plan(cfg_with({"enabled": True, "zoom_start": 1.0, "zoom_end": 1.12}), 10.0, 30)
        self.assertEqual(plan["frames"], 302)
        self.assertAlmostEqual(ken_burns_zoom_at(plan, 1), 1.0 + plan["zoom_step"], places=6)
        self.assertAlmostEqual(ken_burns_zoom_at(plan, plan["frames"]), 1.12, places=6)
        self.assertAlmostEqual(ken_burns_zoom_at(plan, 10 ** 6), 1.12, places=6)
        self.assertIn("min(on,302)/302", plan["z"])
        # Biểu thức phải chứa cả span, không phải bước/frame (bước bị chia thêm lần nữa).
        self.assertIn("0.120000*min(on,302)/302", plan["z"])

    def test_zoom_is_monotonic_and_spread_over_the_whole_clip(self):
        plan = ken_burns_plan(cfg_with(True), 90, 30)
        values = [
            ken_burns_zoom_at(plan, on)
            for on in range(1, plan["frames"] + 1, max(plan["frames"] // 10, 1))
        ]
        self.assertEqual(values, sorted(values))
        self.assertGreater(values[-1], values[0])
        self.assertAlmostEqual(values[-1], plan["zoom_end"], places=2)

    def test_zoom_span_stays_put_while_step_shrinks_with_duration(self):
        """Cùng zoom_start/zoom_end: clip dài hơn thì bước nhỏ hơn, không đứng hình sớm."""
        short = ken_burns_plan(cfg_with(True), 10, 30)
        long = ken_burns_plan(cfg_with(True), 90, 30)
        self.assertAlmostEqual(short["zoom_span"], long["zoom_span"], places=6)
        self.assertGreater(short["zoom_step"], long["zoom_step"])
        self.assertAlmostEqual(ken_burns_zoom_at(long, long["frames"]), long["zoom_end"], places=6)

    def test_pan_direction_picks_axis(self):
        left_right = ken_burns_plan(cfg_with({"pan": "left_right"}), 10, 30)
        self.assertIn("(iw-iw/zoom)*min(on,302)/302", left_right["x"])
        self.assertIn("(ih-ih/zoom)*0.5", left_right["y"])

        right_left = ken_burns_plan(cfg_with({"pan": "right_left"}), 10, 30)
        self.assertIn("(iw-iw/zoom)*(1-min(on,302)/302)", right_left["x"])

        center = ken_burns_plan(cfg_with({"pan": "center"}), 10, 30)
        self.assertIn("(iw-iw/zoom)*0.5", center["x"])
        self.assertIn("(ih-ih/zoom)*0.5", center["y"])

    def test_legacy_boolean_config_still_works(self):
        self.assertTrue(ken_burns_options(cfg_with(True))["enabled"])
        self.assertFalse(ken_burns_options(cfg_with(False))["enabled"])
        self.assertTrue(ken_burns_options(Cfg({"video": {}}))["enabled"])

    def test_unknown_pan_fails_loud(self):
        with self.assertRaises(SystemExit):
            ken_burns_plan(cfg_with({"pan": "cheo_goc"}), 10, 30)

    def test_zoom_end_must_exceed_zoom_start(self):
        with self.assertRaises(SystemExit):
            ken_burns_plan(cfg_with({"zoom_start": 1.2, "zoom_end": 1.1}), 10, 30)

    def test_still_background_uses_zoompan_and_covers_frame(self):
        filt = background_filter(load_config(), 10.0, 30, 1080, 1920, True)
        self.assertIn("zoompan=", filt)
        self.assertIn("d=302", filt)
        self.assertIn("s=1080x1920", filt)
        self.assertIn("force_original_aspect_ratio=increase", filt)
        self.assertIn("crop=1296:2304", filt)

    def test_video_background_has_no_zoompan(self):
        filt = background_filter(load_config(), 10.0, 30, 1080, 1920, False)
        self.assertNotIn("zoompan", filt)
        self.assertIn("crop=1080:1920", filt)


class SegmentedBackgroundTest(unittest.TestCase):
    """Nhiều nền trong cùng 1 clip: mỗi đoạn Ken Burns riêng rồi concat."""

    def test_concat_du_cac_doan_va_ket_thuc_bang_bg(self):
        filt, spans = segmented_background(load_config(), 30.0, 30, 1080, 1920, [True, True, True])
        for index in range(3):
            self.assertIn(f"[{index}:v]", filt)
            self.assertIn(f"[s{index}]", filt)
        self.assertIn("concat=n=3:v=1:a=0[bgc]", filt)
        self.assertTrue(filt.endswith("[bg];"))
        self.assertEqual(len(spans), 3)

    def test_tong_do_dai_dung_bang_duration(self):
        duration = 35.98
        _, spans = segmented_background(load_config(), duration, 30, 1080, 1920, [True, True, True])
        self.assertAlmostEqual(sum(spans), duration, places=9)
        # Đoạn cuối nhận phần lẻ để tổng không hụt
        self.assertGreaterEqual(spans[-1], spans[0])

    def test_anh_tinh_co_zoompan_con_video_thi_khong(self):
        filt, _ = segmented_background(load_config(), 20.0, 30, 1080, 1920, [True, False])
        still_part, video_part = filt.split("[1:v]", 1)
        self.assertIn("zoompan=", still_part)
        self.assertNotIn("zoompan", video_part)
        self.assertIn("fps=30", video_part)

    def test_moi_doan_dung_fps_va_co_khung(self):
        filt, _ = segmented_background(load_config(), 20.0, 30, 1080, 1920, [True, False])
        self.assertEqual(filt.count("fps=30"), 2)
        self.assertIn("s=1080x1920", filt)
        self.assertIn("crop=1080:1920", filt)

    def test_zoompan_dung_d1_de_anh_giu_dung_so_frame(self):
        # d=frames cho từng input sẽ nhân số frame lên N lần → video dài hơn audio.
        filt, _ = segmented_background(load_config(), 20.0, 30, 1080, 1920, [True, True])
        self.assertEqual(filt.count("d=1:"), 2)
        self.assertNotIn("d=302", filt)


class AudioIndexTest(unittest.TestCase):
    def test_chi_so_input_doi_theo_so_nen(self):
        filt = audio_filter(load_config(), 20.0, 19.5, voice_index=3, music_index=4)
        self.assertIn("[3:a]", filt)
        self.assertIn("[4:a]", filt)
        self.assertNotIn("[1:a]", filt)
        self.assertNotIn("[2:a]", filt)

    def test_mac_dinh_van_la_1_va_2(self):
        filt = audio_filter(load_config(), 20.0, 19.5)
        self.assertIn("[1:a]", filt)
        self.assertIn("[2:a]", filt)


if __name__ == "__main__":
    unittest.main()
