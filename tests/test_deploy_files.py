#!/usr/bin/env python3
"""Bộ file deploy: quyền chạy, ENTRYPOINT, volume và những gì image không được thiếu."""

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import yaml  # noqa: E402


class DockerfileTest(unittest.TestCase):
    def setUp(self):
        self.text = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    def test_co_ffmpeg_va_tini(self):
        self.assertIn("ffmpeg", self.text)
        self.assertIn("tini", self.text)

    def test_cai_dependency_tu_requirements(self):
        self.assertIn("requirements.txt", self.text)
        self.assertIn("pip install", self.text)

    def test_build_tu_kiem_tra_doctor_va_compile(self):
        self.assertIn("doctor.py", self.text)
        self.assertIn("compileall", self.text)

    def test_entrypoint_goi_script_co_that(self):
        entrypoint_line = [line for line in self.text.splitlines() if line.startswith("ENTRYPOINT")]
        self.assertTrue(entrypoint_line)
        target = entrypoint_line[0].split('"')[-2]
        relative = target.replace("/app/", "")
        self.assertTrue((ROOT / relative).is_file(), f"ENTRYPOINT trỏ tới {target} không tồn tại")

    def test_entrypoint_co_quyen_chay(self):
        """ENTRYPOINT gọi file trực tiếp: thiếu exec bit là container chết ngay."""
        path = ROOT / "scripts" / "docker-entrypoint.sh"
        self.assertTrue(path.is_file())
        self.assertTrue(os.access(path, os.X_OK), "scripts/docker-entrypoint.sh thiếu exec bit")

    def test_cac_script_khac_cung_co_quyen_chay(self):
        for name in ("prepare_footage.sh", "run_sample.sh", "docker-entrypoint.sh"):
            path = ROOT / "scripts" / name
            self.assertTrue(os.access(path, os.X_OK), f"{name} thiếu exec bit")


class DockerignoreTest(unittest.TestCase):
    def setUp(self):
        self.lines = [
            line.strip() for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        ]

    def test_khong_bo_qua_nhung_thu_image_can(self):
        for required in ("src", "assets", "config.yaml", "prompts", "scripts"):
            self.assertNotIn(required, self.lines, f".dockerignore loại bỏ {required}")

    def test_bo_qua_rac_va_state(self):
        for ignored in (".git", ".env", "logs/*", "data/published.json", "assets/out/*"):
            self.assertIn(ignored, self.lines)

    def test_khong_bo_qua_tests_vi_ci_chay_test_trong_container(self):
        self.assertNotIn("tests", self.lines)


class ComposeTest(unittest.TestCase):
    def setUp(self):
        self.data = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
        self.service = self.data["services"]["reels"]

    def test_mount_asset_du_lieu_va_log(self):
        volumes = " ".join(self.service["volumes"])
        for needed in ("./assets:", "./data:", "./logs:"):
            self.assertIn(needed, volumes)

    def test_doc_env_va_lich_chay(self):
        self.assertIn(".env", self.service["env_file"])
        env = self.service["environment"]
        self.assertIn("SCHEDULE_HOUR", env)
        self.assertIn("SCHEDULE_MINUTE", env)

    def test_restart_khi_loi(self):
        self.assertEqual(self.service["restart"], "unless-stopped")


class EntrypointScriptTest(unittest.TestCase):
    def setUp(self):
        self.text = (ROOT / "scripts" / "docker-entrypoint.sh").read_text(encoding="utf-8")

    def test_chay_doctor_truoc_khi_vao_lich(self):
        self.assertIn("doctor.py", self.text)
        self.assertIn("scheduler.py", self.text)

    def test_co_che_do_nghiem_ngat(self):
        self.assertIn("DOCTOR_STRICT", self.text)
        self.assertIn("exit 1", self.text)

    def test_tao_thu_muc_logs(self):
        self.assertIn("mkdir -p logs", self.text)


if __name__ == "__main__":
    unittest.main()
