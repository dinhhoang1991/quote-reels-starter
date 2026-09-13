#!/usr/bin/env python3
"""Scheduler: số giây tới mốc chạy kế tiếp + vòng chạy hằng ngày."""

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import scheduler  # noqa: E402
from config import Cfg  # noqa: E402


class SecondsUntilTest(unittest.TestCase):
    def test_cung_ngay(self):
        now = datetime(2026, 9, 12, 5, 0, tzinfo=timezone.utc)
        self.assertAlmostEqual(scheduler.seconds_until(7, 0, now), 2 * 3600)

    def test_dung_moc_thi_tinh_cho_ngay_mai(self):
        now = datetime(2026, 9, 12, 7, 0, tzinfo=timezone.utc)
        self.assertAlmostEqual(scheduler.seconds_until(7, 0, now), 24 * 3600)

    def test_qua_moc_thi_sang_ngay_mai(self):
        now = datetime(2026, 9, 12, 8, 30, tzinfo=timezone.utc)
        self.assertAlmostEqual(scheduler.seconds_until(7, 0, now), 22.5 * 3600)

    def test_co_phut(self):
        now = datetime(2026, 9, 12, 6, 45, tzinfo=timezone.utc)
        self.assertAlmostEqual(scheduler.seconds_until(7, 15, now), 30 * 60)

    def test_luon_duong(self):
        for hour in range(24):
            now = datetime(2026, 9, 12, hour, 0, tzinfo=timezone.utc)
            self.assertGreater(scheduler.seconds_until(hour, 0, now), 0)


class SettingsTest(unittest.TestCase):
    def cfg(self, **schedule) -> Cfg:
        return Cfg({"schedule": schedule} if schedule else {})

    def test_lay_tu_config(self):
        self.assertEqual(
            scheduler.schedule_settings(
                self.cfg(hour=6, minute=30, command="x", timezone="Asia/Ho_Chi_Minh")
            ),
            (6, 30, "x", "Asia/Ho_Chi_Minh"),
        )

    def test_mac_dinh_khi_thieu_config(self):
        self.assertEqual(
            scheduler.schedule_settings(self.cfg()),
            (7, 0, "python3 src/publish.py --queue", "UTC"),
        )

    def test_env_ghi_de_config(self):
        cfg = self.cfg(hour=6, minute=30, command="config", timezone="UTC")
        with mock.patch.dict("os.environ", {"SCHEDULE_HOUR": "21", "SCHEDULE_MINUTE": "15",
                                            "SCHEDULE_COMMAND": "env",
                                            "SCHEDULE_TZ": "Asia/Ho_Chi_Minh"}):
            self.assertEqual(scheduler.schedule_settings(cfg), (21, 15, "env", "Asia/Ho_Chi_Minh"))

    def test_timezone_sai_thi_bao_loi_ro(self):
        with self.assertRaises(SystemExit) as ctx:
            scheduler.schedule_settings(self.cfg(timezone="Sai/Vung"))
        self.assertIn("Asia/Ho_Chi_Minh", str(ctx.exception))

    def test_gio_khong_hop_le_thi_bao_loi(self):
        with self.assertRaises(SystemExit):
            scheduler.schedule_settings(self.cfg(hour=24))
        with self.assertRaises(SystemExit):
            scheduler.schedule_settings(self.cfg(minute=99))

    def test_env_khong_phai_so_thi_bao_loi_ro(self):
        cfg = self.cfg(hour=6)
        with (
            mock.patch.dict("os.environ", {"SCHEDULE_HOUR": "bay-gio"}),
            self.assertRaises(SystemExit) as ctx,
        ):
            scheduler.schedule_settings(cfg)
        self.assertIn("SCHEDULE_HOUR", str(ctx.exception))

    def test_env_rong_thi_dung_config(self):
        cfg = self.cfg(hour=6, minute=15)
        with mock.patch.dict("os.environ", {"SCHEDULE_HOUR": "  ", "SCHEDULE_MINUTE": ""}):
            self.assertEqual(
                scheduler.schedule_settings(cfg),
                (6, 15, "python3 src/publish.py --queue", "UTC"),
            )


class TimezoneTest(unittest.TestCase):
    def test_gio_vn_khac_utc_7_tieng(self):
        now = datetime(2026, 9, 13, 0, 0, tzinfo=timezone.utc)   # 07:00 giờ VN
        # đúng mốc 07:00 VN → lần kế tiếp là ngày mai
        self.assertAlmostEqual(
            scheduler.seconds_until(7, 0, now, "Asia/Ho_Chi_Minh"), 24 * 3600
        )
        # 12:00 VN còn 5 tiếng, trong khi 12:00 UTC còn 12 tiếng
        self.assertAlmostEqual(
            scheduler.seconds_until(12, 0, now, "Asia/Ho_Chi_Minh"), 5 * 3600
        )
        self.assertAlmostEqual(scheduler.seconds_until(12, 0, now, "UTC"), 12 * 3600)

    def test_mac_dinh_la_utc(self):
        now = datetime(2026, 9, 13, 6, 0, tzinfo=timezone.utc)
        self.assertAlmostEqual(scheduler.seconds_until(7, 0, now), 3600)

    def test_timezone_sai_thi_bao_loi(self):
        with self.assertRaises(SystemExit):
            scheduler.seconds_until(7, 0, datetime(2026, 9, 13, tzinfo=timezone.utc), "Khong/Co")

    def test_run_daily_truyen_timezone(self):
        # 20:00 UTC = 03:00 giờ VN → còn 4 tiếng tới 07:00 VN
        now = datetime(2026, 9, 12, 20, 0, tzinfo=timezone.utc)
        sleeps: list[float] = []
        with mock.patch.object(scheduler, "log") as log:
            scheduler.run_daily(
                "python3 -c pass", 7, 0, sleep=sleeps.append, now_fn=lambda: now,
                run=lambda argv, check=False: mock.Mock(returncode=0), max_runs=1,
                timezone_name="Asia/Ho_Chi_Minh",
            )
        self.assertAlmostEqual(sleeps[0], 4 * 3600)
        self.assertTrue(any("Asia/Ho_Chi_Minh" in str(call) for call in log.call_args_list))


class RunDailyTest(unittest.TestCase):
    def test_ngu_dung_so_giay_roi_chay_lenh(self):
        now = datetime(2026, 9, 12, 5, 0, tzinfo=timezone.utc)
        sleeps: list[float] = []
        calls: list[list[str]] = []

        def fake_run(argv, check=False):
            calls.append(argv)
            return mock.Mock(returncode=0)

        runs = scheduler.run_daily(
            "python3 src/publish.py --queue", 7, 0,
            sleep=sleeps.append, now_fn=lambda: now, run=fake_run, max_runs=2,
        )
        self.assertEqual(runs, 2)
        self.assertEqual(sleeps, [2 * 3600, 2 * 3600])
        self.assertEqual(calls[0], ["python3", "src/publish.py", "--queue"])

    def test_lenh_loi_van_tiep_tuc_lich(self):
        now = datetime(2026, 9, 12, 5, 0, tzinfo=timezone.utc)
        with mock.patch.object(scheduler, "log") as log:
            runs = scheduler.run_daily(
                "false", 7, 0, sleep=lambda _: None, now_fn=lambda: now,
                run=lambda argv, check=False: mock.Mock(returncode=2), max_runs=1,
            )
        self.assertEqual(runs, 1)
        self.assertTrue(any("exit code 2" in str(call) for call in log.call_args_list))

    def test_command_rong_thi_bao_loi(self):
        with self.assertRaises(SystemExit):
            scheduler.run_daily("   ", 7, 0, sleep=lambda _: None, run=lambda *a, **k: None)

    def test_tach_lenh_co_tham_so(self):
        now = datetime(2026, 9, 12, 5, 0, tzinfo=timezone.utc)
        seen = []
        scheduler.run_daily(
            'python3 src/cleanup.py --apply --days 7', 7, 0, sleep=lambda _: None,
            now_fn=lambda: now, run=lambda argv, check=False: seen.append(argv) or mock.Mock(returncode=0),
            max_runs=1,
        )
        self.assertEqual(seen[0], ["python3", "src/cleanup.py", "--apply", "--days", "7"])


class MainTest(unittest.TestCase):
    def test_once_chay_dung_1_lan(self):
        argv = ["scheduler.py", "--command", "python3 -c pass", "--once"]
        with (
            mock.patch.object(sys, "argv", argv),
            mock.patch.object(scheduler, "load_config",
                              return_value=Cfg({"schedule": {"hour": 7, "minute": 0}})),
            mock.patch.object(scheduler, "run_daily") as run_daily,
        ):
            scheduler.main()
        self.assertEqual(run_daily.call_args.kwargs["max_runs"], 1)

    def test_now_chay_truoc_roi_vao_lich(self):
        argv = ["scheduler.py", "--now", "--command", "python3 -c pass"]
        with (
            mock.patch.object(sys, "argv", argv),
            mock.patch.object(scheduler, "load_config",
                              return_value=Cfg({"schedule": {"hour": 7, "minute": 0}})),
            mock.patch.object(scheduler, "run_daily") as run_daily,
            mock.patch.object(scheduler.subprocess, "run") as run,
        ):
            scheduler.main()
        run.assert_called_once()
        self.assertIsNone(run_daily.call_args.kwargs["max_runs"])

    def test_qua_nua_dem_van_tinh_dung(self):
        """23:59 → 00:05 là 6 phút, không phải gần 24h."""
        now = datetime(2026, 9, 12, 23, 59, tzinfo=timezone.utc)
        self.assertAlmostEqual(scheduler.seconds_until(0, 5, now), 6 * 60)
        just_after = datetime(2026, 9, 13, 0, 0, 1, tzinfo=timezone.utc)
        self.assertAlmostEqual(scheduler.seconds_until(0, 1, just_after), 59)
        at_the_mark = datetime(2026, 9, 13, 0, 1, tzinfo=timezone.utc)
        self.assertAlmostEqual(scheduler.seconds_until(0, 1, at_the_mark), 24 * 3600)
        self.assertAlmostEqual(
            scheduler.seconds_until(7, 0, datetime(2026, 9, 12, 23, 59, tzinfo=timezone.utc)),
            7 * 3600 + 60,
        )


if __name__ == "__main__":
    unittest.main()
