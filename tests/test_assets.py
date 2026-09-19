#!/usr/bin/env python3
"""Chọn nền/nhạc: luân phiên tiến đều, ngẫu nhiên tránh lặp, và state bền qua các lần chạy."""

import json
import random
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import assets  # noqa: E402
from config import Cfg, load_config  # noqa: E402


def files(*names: str) -> list[Path]:
    return [Path("/tmp/footage") / name for name in names]


class ListMediaTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def touch(self, name: str) -> Path:
        path = self.dir / name
        path.write_bytes(b"x")
        return path

    def test_bo_placeholder_va_sai_duoi(self):
        self.touch("b.jpg")
        self.touch("a.png")
        self.touch("_placeholder.jpg")
        self.touch("note.txt")
        got = [p.name for p in assets.list_media(self.dir, assets.FOOTAGE_EXT)]
        self.assertEqual(got, ["a.png", "b.jpg"])

    def test_thu_muc_khong_ton_tai_tra_rong(self):
        self.assertEqual(assets.list_media(self.dir / "khong-co", assets.MUSIC_EXT), [])


class PickFilesTest(unittest.TestCase):
    def test_first_giu_hanh_vi_cu(self):
        picked, state = assets.pick_files("footage", files("a.jpg", "b.jpg", "c.jpg"), 2, "first")
        self.assertEqual([p.name for p in picked], ["a.jpg", "b.jpg"])
        self.assertEqual(state, {})

    def test_rotate_tien_deu_qua_cac_lan_chay(self):
        pool = files("a.jpg", "b.jpg", "c.jpg")
        state: dict = {}
        seen = []
        for _ in range(3):
            picked, state = assets.pick_files("footage", pool, 1, "rotate", state=state)
            seen.append(picked[0].name)
        self.assertEqual(seen, ["a.jpg", "b.jpg", "c.jpg"])
        # Quay vòng lại từ đầu
        picked, state = assets.pick_files("footage", pool, 1, "rotate", state=state)
        self.assertEqual(picked[0].name, "a.jpg")

    def test_rotate_lay_nhieu_file_mot_lan(self):
        pool = files("a.jpg", "b.jpg", "c.jpg", "d.jpg")
        picked, state = assets.pick_files("footage", pool, 2, "rotate")
        self.assertEqual([p.name for p in picked], ["a.jpg", "b.jpg"])
        picked, _ = assets.pick_files("footage", pool, 2, "rotate", state=state)
        self.assertEqual([p.name for p in picked], ["c.jpg", "d.jpg"])

    def test_rotate_khong_lap_khi_so_file_it_hon_so_can(self):
        pool = files("a.jpg", "b.jpg")
        picked, _ = assets.pick_files("footage", pool, 5, "rotate")
        self.assertEqual([p.name for p in picked], ["a.jpg", "b.jpg", "a.jpg", "b.jpg", "a.jpg"])

    def test_random_tranh_file_vua_dung(self):
        pool = files("a.jpg", "b.jpg", "c.jpg")
        state = {"footage": {"recent": ["a.jpg", "b.jpg"]}}
        picked, updated = assets.pick_files("footage", pool, 1, "random", recent=3,
                                            state=state, rng=random.Random(7))
        self.assertEqual(picked[0].name, "c.jpg")
        self.assertIn("c.jpg", updated["footage"]["recent"])

    def test_random_khi_moi_file_deu_vua_dung_thi_van_chon_duoc(self):
        pool = files("a.jpg", "b.jpg")
        state = {"music": {"recent": ["a.jpg", "b.jpg"]}}
        picked, _ = assets.pick_files("music", pool, 1, "random", recent=2,
                                      state=state, rng=random.Random(1))
        self.assertEqual(len(picked), 1)

    def test_random_lay_du_so_file_khong_trung(self):
        pool = files("a.jpg", "b.jpg", "c.jpg")
        picked, _ = assets.pick_files("footage", pool, 3, "random", rng=random.Random(3))
        self.assertEqual(len({p.name for p in picked}), 3)

    def test_danh_sach_rong_tra_rong(self):
        picked, state = assets.pick_files("footage", [], 3, "rotate")
        self.assertEqual(picked, [])
        self.assertEqual(state, {})

    def test_mode_la_thi_bao_loi(self):
        with self.assertRaises(SystemExit):
            assets.pick_files("footage", files("a.jpg"), 1, "linh-tinh")


class SettingsTest(unittest.TestCase):
    def test_mac_dinh_rotate_va_10_giay(self):
        cfg = Cfg({})
        self.assertEqual(assets.settings(cfg)["pick"], "rotate")
        self.assertEqual(assets.settings(cfg)["segment_seconds"], 0.0)

    def test_config_that_co_segment_seconds(self):
        options = assets.settings(load_config())
        self.assertIn(options["pick"], assets.PICK_MODES)
        self.assertGreaterEqual(options["segment_seconds"], 0.0)

    def test_pick_sai_thi_bao_loi(self):
        with self.assertRaises(SystemExit):
            assets.settings(Cfg({"assets": {"pick": "bua"}}))


class StateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "data" / "asset_rotation.json"

    def test_ghi_roi_doc_lai(self):
        assets.save_state(self.path, {"footage": {"offset": 2}})
        self.assertEqual(assets.load_state(self.path), {"footage": {"offset": 2}})

    def test_file_hong_thi_coi_nhu_rong(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("{khong-phai-json", encoding="utf-8")
        self.assertEqual(assets.load_state(self.path), {})

    def test_pick_for_clip_luu_state_va_lan_sau_khac_nen(self):
        cfg = Cfg({"assets": {"pick": "rotate", "state_file": str(self.path)},
                   "paths": {"footage_dir": str(Path(self.tmp.name) / "footage")}})
        pool = files("a.jpg", "b.jpg", "c.jpg")
        first = assets.pick_for_clip("footage", pool, 1, cfg)
        second = assets.pick_for_clip("footage", pool, 1, cfg)
        self.assertNotEqual(first[0].name, second[0].name)
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["footage"]["offset"], 2)

    def test_pick_for_clip_khong_ghi_khi_first(self):
        cfg = Cfg({"assets": {"pick": "first", "state_file": str(self.path)}})
        assets.pick_for_clip("footage", files("a.jpg"), 1, cfg)
        self.assertFalse(self.path.exists())


if __name__ == "__main__":
    unittest.main()
