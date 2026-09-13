#!/usr/bin/env python3
"""Doctor: preflight môi trường, asset, config, hàng chờ, token và 1 clip."""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import doctor  # noqa: E402
from checks import estimate_voice_seconds, parse_rate_percent  # noqa: E402
from config import Cfg, load_config  # noqa: E402
from schema import load_clip, validate_clip  # noqa: E402


def levels(checks, level):
    return [check for check in checks if check.level == level]


def config_with(**overrides) -> Cfg:
    raw = load_config().as_dict()
    for section, values in overrides.items():
        raw[section].update(values)
    return Cfg(raw)


class EstimateTest(unittest.TestCase):
    def test_parse_rate(self):
        self.assertEqual(parse_rate_percent("-8%"), -8.0)
        self.assertEqual(parse_rate_percent("+12%"), 12.0)
        self.assertEqual(parse_rate_percent("0%"), 0.0)
        self.assertEqual(parse_rate_percent("nhanh"), 0.0)

    def test_estimate_khop_ban_doc_that(self):
        data = load_clip(ROOT / "data" / "samples" / "clip_001.json")
        cfg = load_config()
        estimate = estimate_voice_seconds(
            data["voice_script"], cfg.audio.voice_rate, float(cfg.audio.voice_chars_per_second)
        )
        # Bản đọc thật của clip_001 (edge-tts, -8%, 363 ký tự) dài 39.9s.
        self.assertAlmostEqual(estimate, 39.9, delta=4)

    def test_rate_cham_hon_thi_lau_hon(self):
        slow = estimate_voice_seconds("xin chào", "-20%", 9.2)
        normal = estimate_voice_seconds("xin chào", "0%", 9.2)
        self.assertGreater(slow, normal)

    def test_tham_so_khong_dung_duoc_thi_tra_0(self):
        self.assertEqual(estimate_voice_seconds("abc", "-8%", 0), 0.0)
        self.assertEqual(estimate_voice_seconds("abc", "-200%", 9.2), 0.0)


class CheckEnvironmentTest(unittest.TestCase):
    def test_bao_dung_trang_thai_tung_hang_muc(self):
        checks = {check.name: check for check in doctor.check_environment()}
        self.assertEqual(checks["lib PIL"].level, doctor.OK)
        self.assertEqual(checks["lib yaml"].level, doctor.OK)
        self.assertIn(checks["python"].level, {doctor.OK, doctor.FAIL})
        # ffmpeg là điều kiện của máy chạy, không phải của code: chỉ kiểm tra phản ánh đúng.
        expected = doctor.OK if shutil.which("ffmpeg") else doctor.FAIL
        self.assertEqual(checks["ffmpeg"].level, expected)
        self.assertIn("ffprobe", checks)


class CheckConfigTest(unittest.TestCase):
    def test_config_repo_khong_co_fail(self):
        self.assertEqual(levels(doctor.check_config(load_config()), doctor.FAIL), [])

    def test_ken_burns_sai_thi_fail(self):
        checks = doctor.check_config(config_with(video={"ken_burns": {"pan": "cheo_goc"}}))
        ken = [check for check in checks if check.name == "ken burns"]
        self.assertEqual(ken[0].level, doctor.FAIL)

    def test_duration_nguoc_thi_fail(self):
        checks = doctor.check_config(config_with(video={"min_seconds": 90, "max_seconds": 3}))
        duration = [check for check in checks if check.name == "duration"]
        self.assertEqual(duration[0].level, doctor.FAIL)

    def test_max_dai_hon_90s_thi_warn(self):
        checks = doctor.check_config(config_with(video={"max_seconds": 300}))
        duration = [check for check in checks if check.name == "duration"]
        self.assertEqual(duration[0].level, doctor.WARN)

    def test_safe_zone_an_het_khung_thi_fail(self):
        checks = doctor.check_config(config_with(safe_zone={"top": 1000, "bottom": 1000}))
        zone = [check for check in checks if check.name == "safe zone"]
        self.assertEqual(zone[0].level, doctor.FAIL)


class CheckAssetsTest(unittest.TestCase):
    def test_thieu_font_la_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            checks = doctor.check_assets(config_with(paths={"fonts_dir": tmp}))
        self.assertEqual(len(levels(checks, doctor.FAIL)), 1)
        self.assertIn("fonts", levels(checks, doctor.FAIL)[0].name)

    def test_khong_co_footage_va_nhac_thi_warn(self):
        with tempfile.TemporaryDirectory() as tmp:
            checks = doctor.check_assets(
                config_with(paths={"footage_dir": f"{tmp}/f", "music_dir": f"{tmp}/m"})
            )
        warned = {check.name for check in levels(checks, doctor.WARN)}
        self.assertIn("footage", warned)
        self.assertIn("music", warned)

    def test_asset_repo_khong_co_fail(self):
        self.assertEqual(levels(doctor.check_assets(load_config()), doctor.FAIL), [])


class CheckQueueTest(unittest.TestCase):
    def test_repo_queue_khong_co_fail(self):
        self.assertEqual(levels(doctor.check_queue(load_config()), doctor.FAIL), [])

    def test_published_log_hong_thi_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            broken = Path(tmp) / "published.json"
            broken.write_text("{hong", encoding="utf-8")
            checks = doctor.check_queue(config_with(paths={"published_log": str(broken)}))
        self.assertEqual(len(levels(checks, doctor.FAIL)), 1)
        self.assertIn("published log", levels(checks, doctor.FAIL)[0].name)


class CheckClipTest(unittest.TestCase):
    def test_clip_mau_khong_co_fail(self):
        cfg = load_config()
        for name in ("clip_001", "clip_002"):
            checks = doctor.check_clip(cfg, load_clip(ROOT / "data" / "samples" / f"{name}.json"))
            self.assertEqual(levels(checks, doctor.FAIL), [], name)

    def test_noi_dung_qua_dai_thi_canh_bao_nhung_khong_fail(self):
        cfg = load_config()
        data = validate_clip(
            {
                "id": "dai",
                "title": "MỘT HAI BA BỐN NĂM SÁU BẢY TÁM CHÍN MƯỜI",
                "items": [
                    {"label": "Nhãn ngắn", "text": "một hai ba bốn năm sáu bảy tám chín"}
                    for _ in range(2)
                ],
            }
        )
        checks = doctor.check_clip(cfg, data)
        self.assertEqual(levels(checks, doctor.FAIL), [])
        self.assertTrue(levels(checks, doctor.WARN))

    def test_item_khong_the_vua_thi_fail(self):
        cfg = load_config()
        data = validate_clip(
            {
                "id": "tran",
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
            }
        )
        checks = doctor.check_clip(cfg, data)
        overlay = [check for check in checks if check.name == "overlay"]
        self.assertEqual(overlay[0].level, doctor.FAIL)

    def test_canh_bao_voice_qua_dai(self):
        cfg = load_config()
        data = validate_clip(
            {
                "id": "dai",
                "title": "TIÊU ĐỀ",
                "items": [{"label": "Nhãn", "text": "Văn bản"} for _ in range(8)],
                "voice_script": "nội dung rất dài " * 200,
            }
        )
        checks = doctor.check_clip(cfg, data)
        voice = [check for check in checks if check.name == "voice length"]
        self.assertEqual(voice[0].level, doctor.WARN)


class RunAllTest(unittest.TestCase):
    def complete_environment(self):
        """Giả lập máy có đủ ffmpeg/ffprobe để test không phụ thuộc máy chạy."""
        return mock.patch.object(
            doctor.shutil, "which", side_effect=lambda name: f"/usr/bin/{name}"
        )

    def test_run_all_tren_repo_khong_co_fail(self):
        with self.complete_environment():
            checks = doctor.run_all(ROOT / "data" / "samples" / "clip_001.json")
        fails = levels(checks, doctor.FAIL)
        self.assertEqual([f"{check.name}: {check.detail}" for check in fails], [])
        self.assertEqual(doctor.report(checks), 0)

    def test_thieu_ffmpeg_thi_fail(self):
        with mock.patch.object(doctor.shutil, "which", return_value=None):
            checks = doctor.run_all(None)
        self.assertIn("ffmpeg", {check.name for check in levels(checks, doctor.FAIL)})

    def test_report_tra_1_khi_co_fail(self):
        self.assertEqual(doctor.report([doctor.Check(doctor.OK, "a"), doctor.Check(doctor.FAIL, "b")]), 1)

    def test_json_hong_bi_bat(self):
        with tempfile.TemporaryDirectory() as tmp:
            broken = Path(tmp) / "clip.json"
            broken.write_text("{khong-phai-json", encoding="utf-8")
            with self.complete_environment():
                checks = doctor.run_all(broken)
        self.assertEqual(len(levels(checks, doctor.FAIL)), 1)
        self.assertEqual(levels(checks, doctor.FAIL)[0].name, "clip")

    def test_check_token_offline_chi_warn(self):
        with mock.patch.object(doctor, "ROOT", Path(tempfile.gettempdir()) / "khong-co-du-an"):
            checks = doctor.check_token(load_config(), online=False)
        self.assertEqual(levels(checks, doctor.FAIL), [])
        self.assertTrue(levels(checks, doctor.WARN))


class EnvOrderTest(unittest.TestCase):
    """Credential trong .env phải được nạp TRƯỚC check_integrations."""

    def test_doc_duoc_credential_tu_env_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".env").write_text(
                "YOUTUBE_CLIENT_ID=cid\nYOUTUBE_CLIENT_SECRET=sec\nYOUTUBE_REFRESH_TOKEN=ref\n"
                "TIKTOK_ACCESS_TOKEN=tok\n",
                encoding="utf-8",
            )
            cfg = config_with(crosspost={"targets": ["youtube", "tiktok"]})
            with (
                mock.patch.object(doctor, "ROOT", root),
                mock.patch.object(doctor, "load_config", return_value=cfg),
                mock.patch.dict("os.environ", {}, clear=True),
            ):
                all_checks = doctor.run_all(None)
            misses = [
                check for check in all_checks
                if check.name.startswith("credential ") and check.level == doctor.FAIL
            ]
            self.assertEqual([f"{c.name}: {c.detail}" for c in misses], [])

    def test_thieu_credential_thi_bao_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".env").write_text("FB_PAGE_ID=1\n", encoding="utf-8")
            cfg = config_with(crosspost={"targets": ["tiktok"]})
            with (
                mock.patch.object(doctor, "ROOT", root),
                mock.patch.object(doctor, "load_config", return_value=cfg),
                mock.patch.dict("os.environ", {}, clear=True),
            ):
                checks = doctor.check_integrations(cfg)
            fails = [check for check in checks if check.level == doctor.FAIL]
            self.assertEqual(len(fails), 1)
            self.assertIn("TIKTOK_ACCESS_TOKEN", fails[0].detail)


if __name__ == "__main__":
    unittest.main()
