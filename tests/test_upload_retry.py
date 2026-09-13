#!/usr/bin/env python3
"""Retry Graph API: nhận diện lỗi hạn mức (HTTP 400 + code 4/17/32/613) và chờ đúng."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import upload_facebook as fb  # noqa: E402
import upload_youtube as yt  # noqa: E402


class FakeResponse:
    """Response giả đủ dùng cho code retry (không gọi mạng)."""

    def __init__(self, status_code=200, payload=None, headers=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}
        self.text = text or (json.dumps(payload) if payload is not None else "")
        self.content = b"body" if (payload is not None or text) else b""

    def json(self):
        if self._payload is None:
            raise ValueError("không phải JSON")
        return self._payload


def graph_error(code, **extra):
    error = {"code": code, "message": "lỗi giả"}
    error.update(extra)
    return {"error": error}


class TransientTest(unittest.TestCase):
    def test_status_codes(self):
        for status in (408, 429, 500, 502, 503, 504):
            self.assertTrue(fb.is_transient(FakeResponse(status)), status)
        self.assertFalse(fb.is_transient(FakeResponse(400, graph_error(100))))

    def test_meta_flags_and_rate_limit_codes(self):
        self.assertTrue(fb.is_transient(FakeResponse(400, graph_error(100, is_transient=True))))
        for code in (4, 17, 32, 613):
            self.assertTrue(fb.is_transient(FakeResponse(400, graph_error(code))), code)
        self.assertFalse(fb.is_transient(FakeResponse(400, {"error": {"code": 190}})))

    def test_body_khong_phai_json(self):
        self.assertFalse(fb.is_transient(FakeResponse(400, None, text="<html>")))


class RetryDelayTest(unittest.TestCase):
    def test_backoff_luy_thua(self):
        resp = FakeResponse(500)
        self.assertAlmostEqual(fb.retry_delay(resp, 0, 900), 1.5)
        self.assertAlmostEqual(fb.retry_delay(resp, 1, 900), 3.0)
        self.assertAlmostEqual(fb.retry_delay(resp, 3, 900), 12.0)

    def test_ton_trong_estimated_time_to_regain_access(self):
        resp = FakeResponse(
            400, graph_error(613, error_data={"estimated_time_to_regain_access": 2})
        )
        self.assertAlmostEqual(fb.retry_delay(resp, 0, 900), 120.0)

    def test_bi_kep_boi_tran(self):
        resp = FakeResponse(
            400, graph_error(613, error_data={"estimated_time_to_regain_access": 60})
        )
        self.assertAlmostEqual(fb.retry_delay(resp, 0, 300), 300.0)
        self.assertAlmostEqual(fb.retry_delay(FakeResponse(500), 5, 4.0), 4.0)

    def test_gia_tri_rac_thi_roi_ve_backoff(self):
        resp = FakeResponse(400, graph_error(613, error_data={"estimated_time_to_regain_access": "sớm"}))
        self.assertAlmostEqual(fb.retry_delay(resp, 1, 900), 3.0)


class RequestWithRetryTest(unittest.TestCase):
    def test_thu_lai_loi_han_muc_roi_thanh_cong(self):
        responses = [
            FakeResponse(500),
            FakeResponse(400, graph_error(613, error_data={"estimated_time_to_regain_access": 1})),
            FakeResponse(200, {"video_id": "1"}),
        ]
        with mock.patch("upload_facebook.requests.request", side_effect=responses) as request, mock.patch("upload_facebook.time.sleep") as sleep:
                resp = fb.request_with_retry("POST", "https://graph.facebook.com/x", 3)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(request.call_count, 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [1.5, 60.0])

    def test_khong_thu_lai_loi_vinh_vien(self):
        with mock.patch(
            "upload_facebook.requests.request", return_value=FakeResponse(400, graph_error(190))
        ) as request, mock.patch("upload_facebook.time.sleep") as sleep:
            resp = fb.request_with_retry("POST", "https://graph.facebook.com/x", 3)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(request.call_count, 1)
        sleep.assert_not_called()

    def test_het_luot_thi_tra_loi_cuoi(self):
        with mock.patch(
            "upload_facebook.requests.request", return_value=FakeResponse(503)
        ) as request, mock.patch("upload_facebook.time.sleep"):
            resp = fb.request_with_retry("POST", "https://graph.facebook.com/x", 2)
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(request.call_count, 2)


class ApiErrorTest(unittest.TestCase):
    def test_loi_han_muc_co_goi_y(self):
        with self.assertRaises(SystemExit) as ctx:
            raise fb.api_error(FakeResponse(400, graph_error(613)))
        message = str(ctx.exception)
        self.assertIn("613", message)
        self.assertIn("hạn mức", message)

    def test_loi_thuong_khong_co_goi_y(self):
        with self.assertRaises(SystemExit) as ctx:
            raise fb.api_error(FakeResponse(400, graph_error(190)))
        self.assertNotIn("hạn mức", str(ctx.exception))

    def test_body_khong_phai_json_van_doc_duoc(self):
        with self.assertRaises(SystemExit) as ctx:
            raise fb.api_error(FakeResponse(502, None, text="<html>bad gateway</html>"))
        self.assertIn("bad gateway", str(ctx.exception))


class UsageHeaderTest(unittest.TestCase):
    def test_x_app_usage_dang_phang(self):
        resp = FakeResponse(200, {"ok": True}, headers={
            "X-App-Usage": json.dumps({"call_count": 30, "total_time": 20, "total_cputime": 10})
        })
        lines = fb.usage_headers(resp)
        self.assertEqual(len(lines), 1)
        self.assertIn("call=30%", lines[0])
        self.assertIn("cpu=10%", lines[0])

    def test_business_use_case_usage_long_theo_business(self):
        resp = FakeResponse(200, {}, headers={
            "X-Business-Use-Case-Usage": json.dumps({"12345": {"call_count": 5}})
        })
        lines = fb.usage_headers(resp)
        self.assertEqual(len(lines), 1)
        self.assertIn("12345", lines[0])

    def test_header_hong_thi_bo_qua(self):
        resp = FakeResponse(200, {}, headers={"X-App-Usage": "khong-phai-json"})
        self.assertEqual(fb.usage_headers(resp), [])


class UploadBinaryTest(unittest.TestCase):
    def test_upload_lai_khi_loi_tam(self):
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / "clip.mp4"
            video.write_bytes(b"x" * 1024)
            with mock.patch(
                "upload_facebook.requests.request",
                side_effect=[FakeResponse(500), FakeResponse(200, {"success": True})],
            ) as post, mock.patch("upload_facebook.time.sleep") as sleep:
                fb.upload_binary("https://rupload.facebook.com/x", "token", video, 2)
        self.assertEqual(post.call_count, 2)
        sleep.assert_called_once_with(1.5)

    def test_loi_that_thi_bao_ngay(self):
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / "clip.mp4"
            video.write_bytes(b"x")
            with mock.patch(
                "upload_facebook.requests.request", return_value=FakeResponse(403, graph_error(200))
            ) as post, self.assertRaises(SystemExit):
                fb.upload_binary("https://rupload.facebook.com/x", "token", video, 3)
        self.assertEqual(post.call_count, 1)


class RequestFileWithRetryTest(unittest.TestCase):
    """Retry phải gửi lại ĐỦ nội dung file, không phải body rỗng."""

    def test_moi_lan_thu_deu_doc_lai_file_tu_dau(self):
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / "clip.mp4"
            video.write_bytes(b"y" * 4096)
            sizes: list[int] = []

            def fake_request(method, url, headers=None, data=None, timeout=None):
                sizes.append(len(data.read()))
                return FakeResponse(500) if len(sizes) == 1 else FakeResponse(200, {"success": True})

            with (
                mock.patch("upload_facebook.requests.request", side_effect=fake_request),
                mock.patch("upload_facebook.time.sleep"),
            ):
                fb.request_file_with_retry(
                    "PUT", "https://upload/session", video, 2, {"Content-Length": "4096"}, 60
                )
        self.assertEqual(sizes, [4096, 4096], "lần retry phải đọc lại file từ đầu")

    def test_het_luot_thi_tra_response_cuoi(self):
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / "clip.mp4"
            video.write_bytes(b"z" * 10)
            with (
                mock.patch("upload_facebook.requests.request", return_value=FakeResponse(503)),
                mock.patch("upload_facebook.time.sleep"),
            ):
                resp = fb.request_file_with_retry("PUT", "https://upload/session", video, 2, {}, 60)
        self.assertEqual(resp.status_code, 503)

    def test_loi_vinh_vien_tra_ngay(self):
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / "clip.mp4"
            video.write_bytes(b"z" * 10)
            with mock.patch(
                "upload_facebook.requests.request", return_value=FakeResponse(403, graph_error(200))
            ) as request:
                resp = fb.request_file_with_retry("PUT", "https://upload/session", video, 3, {}, 60)
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(request.call_count, 1)

    def test_youtube_upload_dung_helper_chung(self):
        """upload_bytes của YouTube phải đi qua helper mở lại file (bug đã sửa)."""
        from config import Cfg

        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / "clip.mp4"
            video.write_bytes(b"a" * 2048)
            seen: list[int] = []

            def fake_request(method, url, headers=None, data=None, timeout=None):
                seen.append(len(data.read()))
                return FakeResponse(500) if len(seen) == 1 else FakeResponse(200, {"id": "vid1"})

            cfg = Cfg({"facebook": {"max_retries": 2, "retry_max_delay_seconds": 5}})
            with (
                mock.patch("upload_facebook.requests.request", side_effect=fake_request),
                mock.patch("upload_facebook.time.sleep"),
                mock.patch("upload_facebook.load_config", return_value=cfg),
            ):
                resource = yt.upload_bytes("https://upload/session", video, "at", 2)
        self.assertEqual(resource["id"], "vid1")
        self.assertEqual(seen, [2048, 2048])


if __name__ == "__main__":
    unittest.main()
