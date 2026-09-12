#!/usr/bin/env python3
"""Queue: mọi lỗi (kể cả ffmpeg) phải đưa job sang failed/ kèm attempts + last_error."""

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import jobqueue  # noqa: E402
import publish  # noqa: E402
from config import Cfg  # noqa: E402

CLIP = {
    "id": "clip_x",
    "title": "TIÊU ĐỀ",
    "items": [{"label": "Nhãn", "text": "Vài chữ"}],
}


class QueueFailureTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.queue = Path(self.tmp.name) / "queue"
        cfg = Cfg({"paths": {"queue_dir": str(self.queue)}})
        patcher = mock.patch.object(jobqueue, "load_config", return_value=cfg)
        patcher.start()
        self.addCleanup(patcher.stop)
        (self.queue / "pending").mkdir(parents=True)
        self.job = self.queue / "pending" / "clip_x.json"
        self.job.write_text(json.dumps(CLIP), encoding="utf-8")

    def failed_job(self) -> dict:
        return json.loads((self.queue / "failed" / "clip_x.json").read_text(encoding="utf-8"))

    def test_fail_moves_job_and_records_attempt(self):
        dest = jobqueue.fail(self.job, "CalledProcessError: ffmpeg chết")
        self.assertFalse(self.job.exists())
        self.assertEqual(dest, self.queue / "failed" / "clip_x.json")
        meta = self.failed_job()["_queue"]
        self.assertEqual(meta["attempts"], 1)
        self.assertIn("ffmpeg", meta["last_error"])
        self.assertIn("failed_at", meta)

    def test_fail_counts_attempts_across_retries(self):
        first = jobqueue.fail(self.job, "lỗi 1")
        jobqueue.fail(first, "lỗi 2")
        meta = self.failed_job()["_queue"]
        self.assertEqual(meta["attempts"], 2)
        self.assertEqual(meta["last_error"], "lỗi 2")

    def test_fail_tolerates_broken_json(self):
        broken = self.queue / "pending" / "broken.json"
        broken.write_text("{khong phai json", encoding="utf-8")
        dest = jobqueue.fail(broken, "boom")
        data = json.loads(dest.read_text(encoding="utf-8"))
        self.assertEqual(data["id"], "broken")
        self.assertEqual(data["_queue"]["attempts"], 1)

    def test_ffmpeg_crash_moves_job_out_of_pending(self):
        """Lỗi không phải SystemExit (ffmpeg) trước đây để job kẹt trong pending mãi."""
        with mock.patch.object(publish, "publish_one", side_effect=RuntimeError("ffmpeg chết")):
            with mock.patch.object(sys, "argv", ["publish.py", "--queue", "--skip-upload"]):
                with self.assertRaises(RuntimeError):
                    publish.main()
        self.assertFalse(self.job.exists())
        meta = self.failed_job()["_queue"]
        self.assertEqual(meta["attempts"], 1)
        self.assertIn("RuntimeError", meta["last_error"])

    def test_system_exit_still_moves_job_to_failed(self):
        with mock.patch.object(publish, "publish_one", side_effect=SystemExit("hết hạn token")):
            with mock.patch.object(sys, "argv", ["publish.py", "--queue", "--skip-upload"]):
                with self.assertRaises(SystemExit):
                    publish.main()
        self.assertIn("hết hạn token", self.failed_job()["_queue"]["last_error"])

    def test_success_moves_job_to_done(self):
        with mock.patch.object(publish, "publish_one") as fake:
            with mock.patch.object(sys, "argv", ["publish.py", "--queue", "--skip-upload"]):
                publish.main()
        fake.assert_called_once()
        self.assertTrue((self.queue / "done" / "clip_x.json").exists())
        self.assertFalse(self.job.exists())

    def test_describe_error(self):
        self.assertEqual(publish.describe_error(SystemExit("hết hạn token")), "hết hạn token")
        self.assertEqual(publish.describe_error(SystemExit()), "SystemExit")
        self.assertEqual(publish.describe_error(ValueError("xấu")), "ValueError: xấu")


class QueueLockTest(QueueFailureTest):
    def test_lock_chan_lan_chay_thu_hai(self):
        with jobqueue.queue_lock() as path:
            self.assertTrue(path.exists())
            with self.assertRaises(SystemExit):
                with jobqueue.queue_lock():
                    self.fail("không được lấy lock lần hai")
        self.assertFalse(path.exists())
        # Thoát ra là lấy lại được.
        with jobqueue.queue_lock():
            pass

    def test_lock_cu_bi_lay_lai(self):
        path = jobqueue.lock_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("99999 process đã chết\n", encoding="utf-8")
        old = time.time() - 7200
        os.utime(path, (old, old))
        with jobqueue.queue_lock(stale_seconds=60) as locked:
            self.assertEqual(locked, path)
        self.assertFalse(path.exists())

    def test_lock_moi_thi_khong_bi_lay(self):
        path = jobqueue.lock_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("99999\n", encoding="utf-8")
        with self.assertRaises(SystemExit):
            with jobqueue.queue_lock(stale_seconds=3600):
                self.fail("lock mới thì không được lấy")
        path.unlink()

    def test_publish_queue_nha_lock_sau_khi_chay(self):
        path = jobqueue.lock_path()
        with mock.patch.object(publish, "publish_one"):
            with mock.patch.object(sys, "argv", ["publish.py", "--queue", "--skip-upload"]):
                publish.main()
        self.assertFalse(path.exists())
        self.assertFalse(self.job.exists())


if __name__ == "__main__":
    unittest.main()
