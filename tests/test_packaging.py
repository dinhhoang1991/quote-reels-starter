#!/usr/bin/env python3
"""Đóng gói: requirements.txt và pyproject.toml phải khớp nhau, CI phải chạy test."""

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ModuleNotFoundError:
        tomllib = None  # type: ignore[assignment]

import yaml  # noqa: E402


def requirement_entries(path: Path | None = None) -> list[str]:
    """Các dòng dependency trong file requirements (bỏ comment/dòng trống)."""
    lines = (path or ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.startswith(("#", "-"))]


def normalized(spec: str) -> str:
    return re.sub(r"[\s_-]", "", spec).lower()


@unittest.skipIf(tomllib is None, "cần tomllib (Python 3.11+) hoặc tomli")
class PyprojectTest(unittest.TestCase):
    def setUp(self):
        self.data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.project = self.data["project"]

    def test_requirements_khop_voi_pyproject(self):
        from_requirements = sorted(normalized(item) for item in requirement_entries())
        from_pyproject = sorted(normalized(item) for item in self.project["dependencies"])
        self.assertEqual(from_requirements, from_pyproject)

    def test_dependency_duoc_pin_chinh_xac(self):
        for spec in self.project["dependencies"]:
            self.assertRegex(spec, r"^[A-Za-z0-9._-]+==\d", spec)

    def test_requires_python_khop_voi_ruff(self):
        self.assertEqual(self.project["requires-python"], ">=3.10")
        self.assertEqual(self.data["tool"]["ruff"]["target-version"], "py310")

    def test_metadata_co_ten_va_giay_phep(self):
        self.assertEqual(self.project["name"], "quote-reels-starter")
        self.assertEqual(self.project["license"]["text"], "MIT")
        self.assertTrue(self.project["version"])

    def test_license_ghi_dung_tac_gia(self):
        license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
        self.assertIn("Copyright (c) 2026 dinhhoang1991", license_text)


class WorkflowTest(unittest.TestCase):
    def setUp(self):
        path = ROOT / ".github" / "workflows" / "ci.yml"
        self.workflow = yaml.safe_load(path.read_text(encoding="utf-8"))

    def test_ci_chay_unittest_va_ruff(self):
        steps = [
            step
            for job in self.workflow["jobs"].values()
            for step in job["steps"]
        ]
        runs = " ".join(str(step.get("run", "")) for step in steps)
        self.assertIn("unittest discover -s tests -t .", runs)
        self.assertIn("ruff check src tests", runs)

    def test_ci_cai_ruff_tu_requirements_dev(self):
        """Ruff phải được pin, không cài bản mới nhất trôi nổi."""
        lint_job = self.workflow["jobs"]["lint"]
        installs = " ".join(str(step.get("run", "")) for step in lint_job["steps"])
        self.assertIn("requirements-dev.txt", installs)
        dev = requirement_entries(ROOT / "requirements-dev.txt")
        ruff = [item for item in dev if item.lower().startswith("ruff")]
        self.assertEqual(len(ruff), 1)
        self.assertRegex(ruff[0], r"^ruff==\d")

    def test_ci_phu_cac_phien_bang_python_ho_tro(self):
        matrix = self.workflow["jobs"]["test"]["strategy"]["matrix"]["python-version"]
        self.assertEqual(matrix, ["3.10", "3.11", "3.12", "3.13"])

    def test_ci_khong_cai_ffmpeg_cho_job_test(self):
        """Test phải chạy được trên máy chưa có FFmpeg (job doctor mới cài)."""
        test_job = self.workflow["jobs"]["test"]
        runs = " ".join(str(step.get("run", "")) for step in test_job["steps"])
        self.assertNotIn("ffmpeg", runs.lower())

    def test_dependabot_theo_doi_pip(self):
        data = yaml.safe_load((ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8"))
        ecosystems = {entry["package-ecosystem"] for entry in data["updates"]}
        self.assertIn("pip", ecosystems)


if __name__ == "__main__":
    unittest.main()
