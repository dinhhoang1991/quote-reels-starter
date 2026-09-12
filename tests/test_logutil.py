#!/usr/bin/env python3
"""Publish log: ghi atomic, hạn mức 24h chỉ tính bài đã publish, log có timestamp."""

import io
import json
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import logutil  # noqa: E402
from config import Cfg  # noqa: E402


class LogUtilTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.log_file = Path(self.tmp.name) / "published.json"
        cfg = Cfg({"paths": {"published_log": str(self.log_file)}, "facebook": {"daily_limit": 30}})
        patcher = mock.patch.object(logutil, "load_config", return_value=cfg)
        patcher.start()
        self.addCleanup(patcher.stop)

    def write_posts(self, published: int, drafts: int) -> None:
        posts = [
            {"clip_id": f"p{i}", "state": "PUBLISHED", "ts": time.time() - 60, "video_id": str(i)}
            for i in range(published)
        ]
        posts += [
            {"clip_id": f"d{i}", "state": "DRAFT", "ts": time.time() - 60, "video_id": f"d{i}"}
            for i in range(drafts)
        ]
        self.log_file.write_text(json.dumps({"posts": posts}), encoding="utf-8")

    def test_save_log_ghi_qua_file_tam_roi_replace(self):
        real_replace = logutil.os.replace
        with mock.patch.object(logutil.os, "replace", side_effect=real_replace) as replace:
            logutil.save_log({"posts": [{"clip_id": "x"}]})
        self.assertEqual(replace.call_count, 1)
        source, dest = replace.call_args.args
        self.assertEqual(Path(dest), self.log_file)
        self.assertEqual(Path(source).parent, self.log_file.parent)
        self.assertFalse(Path(source).exists())
        self.assertEqual(json.loads(self.log_file.read_text(encoding="utf-8"))["posts"][0]["clip_id"], "x")

    def test_quota_chi_tinh_bai_da_publish(self):
        self.write_posts(published=29, drafts=10)
        self.assertEqual(logutil.remaining_quota(), 1)
        self.assertEqual(len(logutil.posts_last_24h()), 29)

    def test_quota_het_khi_du_30_bai_publish(self):
        self.write_posts(published=30, drafts=0)
        self.assertEqual(logutil.remaining_quota(), 0)
        with self.assertRaises(SystemExit):
            logutil.assert_can_publish("clip_moi")

    def test_posts_last_24h_bo_bai_cu_hon_24h(self):
        old = time.time() - 25 * 3600
        self.log_file.write_text(
            json.dumps({"posts": [{"clip_id": "cu", "state": "PUBLISHED", "ts": old}]}),
            encoding="utf-8",
        )
        self.assertEqual(logutil.posts_last_24h(), [])
        self.assertEqual(logutil.remaining_quota(), 30)

    def test_chong_dang_trung_chi_chan_bai_published(self):
        self.write_posts(published=0, drafts=0)
        self.log_file.write_text(
            json.dumps({"posts": [{"clip_id": "clip_x", "state": "DRAFT", "ts": time.time()}]}),
            encoding="utf-8",
        )
        logutil.assert_can_publish("clip_x")
        self.log_file.write_text(
            json.dumps({"posts": [{"clip_id": "clip_x", "state": "PUBLISHED", "ts": time.time()}]}),
            encoding="utf-8",
        )
        with self.assertRaises(SystemExit):
            logutil.assert_can_publish("clip_x")
        logutil.assert_can_publish("clip_x", force=True)

    def test_json_hong_khong_lam_sap_pipeline(self):
        self.log_file.write_text("{hong", encoding="utf-8")
        self.assertEqual(logutil.load_log(), {"posts": []})
        self.assertEqual(logutil.remaining_quota(), 30)

    def test_log_co_timestamp_utc(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            logutil.log("xin chào")
        line = buffer.getvalue().strip()
        self.assertRegex(line, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z xin chào$")


if __name__ == "__main__":
    unittest.main()
