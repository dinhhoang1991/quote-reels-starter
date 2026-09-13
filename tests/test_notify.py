#!/usr/bin/env python3
"""Cảnh báo Telegram/webhook: gửi đúng payload, không bao giờ làm hỏng pipeline."""

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import notify as notify_module  # noqa: E402
import publish  # noqa: E402
import scheduler  # noqa: E402
from config import Cfg  # noqa: E402
from notify import (  # noqa: E402
    format_message,
    notify,
    notify_empty_queue,
    notify_publish_failure,
    notify_publish_success,
    notify_token_expiring,
    should_send,
    telegram_payload,
)


def cfg_with(**notify_section) -> Cfg:
    return Cfg({"notify": notify_section} if notify_section else {})


def fake_response(status_code=200, text="ok"):
    return type("R", (), {"status_code": status_code, "text": text})()


class EnvMixin(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = cfg_with()
        patcher = mock.patch.object(notify_module, "ROOT", Path(self.tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        env_patcher = mock.patch.dict("os.environ", {}, clear=True)
        env_patcher.start()
        self.addCleanup(env_patcher.stop)
        # không đọc .env thật trong test
        self.load_env_patcher = mock.patch("upload_facebook.load_env")
        self.load_env_patcher.start()
        self.addCleanup(self.load_env_patcher.stop)


class FormatTest(unittest.TestCase):
    def test_co_tag_muc_do_va_clip(self):
        self.assertTrue(format_message("lỗi rồi", "error", "clip_001").startswith("❌ [clip_001]"))
        self.assertIn("⚠️", format_message("cẩn thận", "warn"))
        self.assertIn("ℹ️", format_message("bình thường", "info"))

    def test_gop_khoang_trang(self):
        self.assertEqual(format_message("a\n\n  b", "warn"), "⚠️ a b")

    def test_telegram_payload(self):
        payload = telegram_payload("123", "nội dung")
        self.assertEqual(payload["chat_id"], "123")
        self.assertEqual(payload["text"], "nội dung")
        self.assertTrue(payload["disable_web_page_preview"])

    def test_nguong_muc_do(self):
        self.assertTrue(should_send("error", "warn"))
        self.assertTrue(should_send("warn", "warn"))
        self.assertFalse(should_send("info", "warn"))
        self.assertTrue(should_send("info", "info"))


class NotifyTest(EnvMixin):
    def test_chua_cau_hinh_thi_khong_goi_mang(self):
        with mock.patch("requests.post") as post:
            self.assertEqual(notify("xin chào", cfg=self.cfg), {})
        post.assert_not_called()

    def test_telegram_gui_dung_url_va_body(self):
        with (
            mock.patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "tok", "TELEGRAM_CHAT_ID": "42"}),
            mock.patch("requests.post", return_value=fake_response()) as post,
        ):
            result = notify("lỗi rồi", level="error", clip_id="c1", cfg=self.cfg)
        self.assertTrue(result["telegram"]["ok"])
        url = post.call_args.args[0]
        self.assertIn("/bottok/sendMessage", url)
        self.assertEqual(post.call_args.kwargs["json"]["chat_id"], "42")
        self.assertIn("c1", post.call_args.kwargs["json"]["text"])

    def test_webhook_gui_json(self):
        with (
            mock.patch.dict("os.environ", {"NOTIFY_WEBHOOK_URL": "https://vi-du/hook"}),
            mock.patch("requests.post", return_value=fake_response()) as post,
        ):
            result = notify("cảnh báo", level="warn", clip_id="c9", cfg=self.cfg)
        self.assertTrue(result["webhook"]["ok"])
        self.assertEqual(post.call_args.args[0], "https://vi-du/hook")
        body = post.call_args.kwargs["json"]
        self.assertEqual(body["level"], "warn")
        self.assertEqual(body["clip_id"], "c9")

    def test_gui_ca_hai_kenh(self):
        env = {"TELEGRAM_BOT_TOKEN": "tok", "TELEGRAM_CHAT_ID": "1", "NOTIFY_WEBHOOK_URL": "https://w"}
        with mock.patch.dict("os.environ", env), mock.patch("requests.post", return_value=fake_response()) as post:
            result = notify("x", cfg=self.cfg)
        self.assertEqual(set(result), {"telegram", "webhook"})
        self.assertEqual(post.call_count, 2)

    def test_loi_mang_khong_lam_vo_va_kenh_khac_van_gui(self):
        def fake_post(url, json=None, timeout=None):
            if "telegram" in url:
                raise ConnectionError("mạng hỏng")
            return fake_response()

        env = {"TELEGRAM_BOT_TOKEN": "tok", "TELEGRAM_CHAT_ID": "1", "NOTIFY_WEBHOOK_URL": "https://w"}
        with mock.patch.dict("os.environ", env), mock.patch("requests.post", side_effect=fake_post):
            result = notify("x", cfg=self.cfg)
        self.assertIn("error", result["telegram"])
        self.assertTrue(result["webhook"]["ok"])

    def test_http_loi_duoc_ghi_lai(self):
        with (
            mock.patch.dict("os.environ", {"NOTIFY_WEBHOOK_URL": "https://w"}),
            mock.patch("requests.post", return_value=fake_response(500, "server error")),
        ):
            result = notify("x", cfg=self.cfg)
        self.assertIn("HTTP 500", result["webhook"]["error"])

    def test_tat_trong_config(self):
        with (
            mock.patch.dict("os.environ", {"NOTIFY_WEBHOOK_URL": "https://w"}),
            mock.patch("requests.post") as post,
        ):
            self.assertEqual(notify("x", cfg=cfg_with(enabled=False)), {})
        post.assert_not_called()

    def test_duoi_nguong_thi_khong_gui(self):
        with (
            mock.patch.dict("os.environ", {"NOTIFY_WEBHOOK_URL": "https://w"}),
            mock.patch("requests.post") as post,
        ):
            self.assertEqual(notify("x", level="info", cfg=cfg_with(min_level="error")), {})
        post.assert_not_called()

    def test_dry_run_khong_goi_mang(self):
        with (
            mock.patch.dict("os.environ", {"NOTIFY_WEBHOOK_URL": "https://w"}),
            mock.patch("requests.post") as post,
        ):
            result = notify("x", cfg=self.cfg, dry_run=True)
        self.assertTrue(result["webhook"]["dry_run"])
        post.assert_not_called()

    def test_helpers_theo_config(self):
        with mock.patch.dict("os.environ", {"NOTIFY_WEBHOOK_URL": "https://w"}), mock.patch(
            "requests.post", return_value=fake_response()
        ) as post:
            notify_publish_success("c1", "https://reel/1")   # on_success mặc định false
            self.assertEqual(post.call_count, 0)
            notify_empty_queue()                              # on_empty_queue mặc định true
            self.assertEqual(post.call_count, 1)
            notify_publish_failure("c1", "ffmpeg chết")
            self.assertEqual(post.call_count, 2)
            notify_token_expiring(3)
            self.assertEqual(post.call_count, 3)
            self.assertIn("token", post.call_args.kwargs["json"]["text"])

    def test_on_success_bat_thi_gui(self):
        with (
            mock.patch.dict("os.environ", {"NOTIFY_WEBHOOK_URL": "https://w"}),
            mock.patch.object(notify_module, "load_config", return_value=cfg_with(on_success=True)),
            mock.patch("requests.post", return_value=fake_response()) as post,
        ):
            notify_publish_success("c1", "https://reel/1")
        self.assertEqual(post.call_count, 1)


class CliTest(EnvMixin):
    def test_in_json_ket_qua(self):
        buffer = io.StringIO()
        with (
            mock.patch.object(sys, "argv", ["notify.py", "thử", "--dry-run"]),
            mock.patch.dict("os.environ", {"NOTIFY_WEBHOOK_URL": "https://w"}),
            mock.patch.object(notify_module, "load_config", return_value=self.cfg),
            redirect_stdout(buffer),
        ):
            notify_module.main()
        self.assertIn("webhook", buffer.getvalue())


class WiringTest(EnvMixin):
    def test_publish_loi_thi_canh_bao(self):
        with (
            mock.patch.object(publish, "notify_publish_failure") as alert,
            mock.patch.object(publish, "publish_one", side_effect=RuntimeError("ffmpeg chết")),
            mock.patch.object(publish, "queue_fail", return_value=Path("/tmp/x")),
            mock.patch.object(publish, "next_pending", return_value=Path("/tmp/clip_001.json")),
            mock.patch.object(publish, "load_config", return_value=Cfg({"queue": {}})),
            mock.patch.object(sys, "argv", ["publish.py", "--queue"]),
            self.assertRaises(SystemExit) as ctx,
        ):
            publish.main()
        self.assertEqual(ctx.exception.code, 1)   # batch báo lỗi bằng exit code
        alert.assert_called_once()
        self.assertEqual(alert.call_args.args[0], "clip_001")

    def test_scheduler_lenh_loi_thi_canh_bao(self):
        from datetime import datetime, timezone

        now = datetime(2026, 9, 13, 5, 0, tzinfo=timezone.utc)
        with mock.patch.object(scheduler, "notify") as alert:
            scheduler.run_daily(
                "false", 7, 0, sleep=lambda _: None, now_fn=lambda: now,
                run=lambda argv, check=False: mock.Mock(returncode=2), max_runs=1,
            )
        alert.assert_called_once()
        self.assertIn("exit code 2", alert.call_args.args[0])

    def test_scheduler_khong_chay_duoc_lenh_thi_canh_bao(self):
        from datetime import datetime, timezone

        now = datetime(2026, 9, 13, 5, 0, tzinfo=timezone.utc)

        def boom(argv, check=False):
            raise FileNotFoundError("không có python3")

        with mock.patch.object(scheduler, "notify") as alert:
            runs = scheduler.run_daily(
                "python3 x", 7, 0, sleep=lambda _: None, now_fn=lambda: now, run=boom, max_runs=1
            )
        self.assertEqual(runs, 1)
        self.assertIn("Không chạy được lệnh", alert.call_args.args[0])

    def test_doctor_canh_bao_token_sap_het_han(self):
        import time

        import doctor

        cfg = Cfg({"facebook": {"api_version": "v26.0"}})
        soon = time.time() + 3 * 86400
        debug = {"is_valid": True, "expires_at": soon, "scopes": [
            "pages_show_list", "pages_read_engagement", "pages_manage_posts"]}
        with (
            mock.patch.object(doctor, "ROOT", Path(self.tmp.name)),
            mock.patch.object(doctor, "_debug_token", return_value=debug),
            mock.patch.dict("os.environ", {"FB_PAGE_ID": "1", "FB_PAGE_ACCESS_TOKEN": "tok",
                                           "FB_APP_ID": "a", "FB_APP_SECRET": "s"}, clear=True),
            mock.patch("notify.notify_token_expiring") as alert,
        ):
            (Path(self.tmp.name) / ".env").write_text(".env giả\n", encoding="utf-8")
            checks = doctor.check_token(cfg, online=True)
        self.assertTrue(any(check.level == doctor.WARN for check in checks))
        alert.assert_called_once()


if __name__ == "__main__":
    unittest.main()
