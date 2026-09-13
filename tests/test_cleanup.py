#!/usr/bin/env python3
"""Dọn file cũ: chỉ xoá file quá hạn, không đụng clip đang trong queue."""

import io
import json
import os
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import cleanup  # noqa: E402
import logutil  # noqa: E402
from config import Cfg  # noqa: E402

DAYS = 86400.0


class TempTree:
    """Cây thư mục tạm giống repo: assets/{voice,overlays,out} + data/queue."""

    def __init__(self, base: Path):
        self.base = base
        self.voice = base / "assets" / "voice"
        self.overlays = base / "assets" / "overlays"
        self.out = base / "assets" / "out"
        self.queue = base / "data" / "queue"
        for folder in (self.voice, self.overlays, self.out, self.queue / "pending",
                       self.queue / "failed", self.queue / "done"):
            folder.mkdir(parents=True, exist_ok=True)
        self.published = base / "data" / "published.json"
        self.cfg = Cfg({
            "paths": {
                "voice_dir": str(self.voice),
                "overlay_dir": str(self.overlays),
                "out_dir": str(self.out),
                "queue_dir": str(self.queue),
                "published_log": str(self.published),
            },
            "cleanup": {"keep_days": 30},
        })

    def add(self, folder: Path, name: str, age_days: float, size: int = 10) -> Path:
        path = folder / name
        path.write_bytes(b"x" * size)
        stamp = time.time() - age_days * DAYS
        os.utime(path, (stamp, stamp))
        return path

    def queue_job(self, status: str, clip_id: str) -> Path:
        path = self.queue / status / f"{clip_id}.json"
        path.write_text(json.dumps({
            "id": clip_id, "title": "T", "items": [{"label": "a", "text": "b"}],
        }), encoding="utf-8")
        return path

    def publish(self, clip_id: str) -> None:
        data = {"id": clip_id, "title": "T", "items": [{"label": "a", "text": "b"}]}
        logutil.record_publish(clip_id, "vid", "PUBLISHED", "https://x", {}, data=data)


class ClipIdTest(unittest.TestCase):
    def test_tach_id_tu_cac_dang_ten_file(self):
        self.assertEqual(cleanup.clip_id_of(Path("clip_001.646d2e17d5.mp3")), "clip_001")
        self.assertEqual(cleanup.clip_id_of(Path("clip_001_hook.png")), "clip_001")
        self.assertEqual(cleanup.clip_id_of(Path("clip_001_cover.jpg")), "clip_001")
        self.assertEqual(cleanup.clip_id_of(Path("clip_001.png")), "clip_001")
        self.assertEqual(cleanup.clip_id_of(Path("clip_001.mp4")), "clip_001")

    def test_ten_khong_khop_thi_tra_nguyen_stem(self):
        self.assertEqual(cleanup.clip_id_of(Path("khong_ro.png")), "khong_ro")
        # hậu tố không phải hex thì giữ nguyên
        self.assertEqual(cleanup.clip_id_of(Path("clip.ban-cuoi.mp3")), "clip.ban-cuoi")


class CollectTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tree = TempTree(Path(self.tmp.name))
        for patcher in (
            mock.patch.object(cleanup, "load_config", return_value=self.tree.cfg),
            mock.patch.object(logutil, "load_config", return_value=self.tree.cfg),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def names(self, candidates) -> set[str]:
        return {item.path.name for item in candidates}

    def test_chi_lay_file_qua_han(self):
        self.tree.add(self.tree.voice, "clip_cu.aaaa1111.mp3", age_days=40)
        self.tree.add(self.tree.voice, "clip_moi.bbbb2222.mp3", age_days=1)
        self.tree.add(self.tree.out, "clip_cu.mp4", age_days=90)
        self.assertEqual(self.names(cleanup.collect(self.tree.cfg)), {"clip_cu.aaaa1111.mp3", "clip_cu.mp4"})

    def test_khong_dung_toi_clip_dang_trong_queue(self):
        self.tree.add(self.tree.voice, "clip_cho.aaaa1111.mp3", age_days=40)
        self.tree.add(self.tree.out, "clip_loi.mp4", age_days=40)
        self.tree.add(self.tree.out, "clip_xong.mp4", age_days=40)
        self.tree.queue_job("pending", "clip_cho")
        self.tree.queue_job("failed", "clip_loi")
        self.tree.queue_job("done", "clip_xong")
        self.assertEqual(self.names(cleanup.collect(self.tree.cfg)), {"clip_xong.mp4"})

    def test_json_queue_hong_van_bao_ve_theo_ten_file(self):
        self.tree.add(self.tree.voice, "clip_hong.aaaa1111.mp3", age_days=40)
        (self.tree.queue / "pending" / "clip_hong.json").write_text("{hong", encoding="utf-8")
        self.assertEqual(cleanup.collect(self.tree.cfg), [])

    def test_bo_qua_placeholder_va_gitkeep(self):
        self.tree.add(self.tree.voice, "_placeholder.mp3", age_days=99)
        self.tree.add(self.tree.overlays, ".gitkeep", age_days=99)
        self.assertEqual(cleanup.collect(self.tree.cfg), [])

    def test_don_ca_anh_cover_jpg(self):
        """Ảnh cover (.jpg) trước đây không bao giờ được dọn."""
        self.tree.add(self.tree.overlays, "clip_001_cover.jpg", age_days=40)
        self.tree.add(self.tree.overlays, "clip_002_hook.png", age_days=40)
        self.assertEqual(
            self.names(cleanup.collect(self.tree.cfg)),
            {"clip_001_cover.jpg", "clip_002_hook.png"},
        )

    def test_keep_days_am_thi_bao_loi(self):
        """Số âm sẽ xoá mọi file cũ — phải chặn trước khi xoá."""
        self.tree.add(self.tree.out, "clip_a.mp4", age_days=1)
        with self.assertRaises(SystemExit) as ctx:
            cleanup.collect(self.tree.cfg, keep_days=-1)
        self.assertIn("keep_days", str(ctx.exception))

    def test_keep_days_0_van_hop_le(self):
        self.tree.add(self.tree.out, "clip_a.mp4", age_days=1)
        self.assertEqual(len(cleanup.collect(self.tree.cfg, keep_days=0)), 1)

    def test_ly_do_da_dang_hay_chua(self):
        self.tree.publish("clip_dadang")
        self.tree.add(self.tree.out, "clip_dadang.mp4", age_days=40)
        self.tree.add(self.tree.out, "clip_chuadang.mp4", age_days=40)
        reasons = {item.path.name: item.reason for item in cleanup.collect(self.tree.cfg)}
        self.assertEqual(reasons["clip_dadang.mp4"], "đã đăng")
        self.assertEqual(reasons["clip_chuadang.mp4"], "chưa đăng nhưng quá cũ")

    def test_keep_days_ghi_de_duoc(self):
        self.tree.add(self.tree.out, "clip.mp4", age_days=10)
        self.assertEqual(cleanup.collect(self.tree.cfg, keep_days=30), [])
        self.assertEqual(len(cleanup.collect(self.tree.cfg, keep_days=5)), 1)

    def test_sap_xep_cu_nhat_truoc(self):
        self.tree.add(self.tree.out, "clip_a.mp4", age_days=40)
        self.tree.add(self.tree.out, "clip_b.mp4", age_days=80)
        ages = [item.age_days for item in cleanup.collect(self.tree.cfg)]
        self.assertEqual(ages, sorted(ages, reverse=True))


class ApplyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tree = TempTree(Path(self.tmp.name))
        for patcher in (
            mock.patch.object(cleanup, "load_config", return_value=self.tree.cfg),
            mock.patch.object(logutil, "load_config", return_value=self.tree.cfg),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_xoa_va_tra_ve_dung_so_lieu(self):
        self.tree.add(self.tree.out, "clip_a.mp4", age_days=40, size=100)
        self.tree.add(self.tree.voice, "clip_b.aaaa1111.mp3", age_days=40, size=50)
        candidates = cleanup.collect(self.tree.cfg)
        removed, freed = cleanup.apply_cleanup(candidates)
        self.assertEqual((removed, freed), (2, 150))
        self.assertFalse((self.tree.out / "clip_a.mp4").exists())

    def test_file_bien_mat_truoc_khi_xoa_thi_bo_qua(self):
        self.tree.add(self.tree.out, "clip_a.mp4", age_days=40)
        candidates = cleanup.collect(self.tree.cfg)
        candidates[0].path.unlink()
        removed, freed = cleanup.apply_cleanup(candidates)
        self.assertEqual((removed, freed), (0, 0))

    def test_report_noi_ro_dry_run_hay_apply(self):
        self.tree.add(self.tree.out, "clip_a.mp4", age_days=40, size=2048)
        candidates = cleanup.collect(self.tree.cfg)
        self.assertIn("sẽ xoá 1 file", cleanup.report(candidates, 30, applied=False))
        self.assertIn("đã xoá 1 file", cleanup.report(candidates, 30, applied=True))


class MainTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tree = TempTree(Path(self.tmp.name))
        for patcher in (
            mock.patch.object(cleanup, "load_config", return_value=self.tree.cfg),
            mock.patch.object(logutil, "load_config", return_value=self.tree.cfg),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_main(self, argv: list[str]) -> str:
        buffer = io.StringIO()
        with (
            mock.patch.object(sys, "argv", ["cleanup.py", *argv]),
            redirect_stdout(buffer),
            self.assertRaises(SystemExit) as ctx,
        ):
            cleanup.main()
        self.assertEqual(ctx.exception.code, 0)
        return buffer.getvalue()

    def test_mac_dinh_chi_liet_ke(self):
        path = self.tree.add(self.tree.out, "clip_a.mp4", age_days=40)
        out = self.run_main([])
        self.assertIn("sẽ xoá 1 file", out)
        self.assertIn("--apply", out)
        self.assertTrue(path.exists())

    def test_apply_xoa_that(self):
        path = self.tree.add(self.tree.out, "clip_a.mp4", age_days=40)
        out = self.run_main(["--apply"])
        self.assertIn("đã xoá 1 file", out)
        self.assertFalse(path.exists())

    def test_days_ghi_de(self):
        self.tree.add(self.tree.out, "clip_a.mp4", age_days=10)
        self.assertIn("sẽ xoá 0 file", self.run_main(["--days", "30"]))
        self.assertIn("sẽ xoá 1 file", self.run_main(["--days", "5"]))


if __name__ == "__main__":
    unittest.main()
