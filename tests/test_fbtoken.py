#!/usr/bin/env python3
"""fbttoken: đổi token ngắn hạn → dài hạn, liệt kê Page token (HTTP giả)."""

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import fbtoken  # noqa: E402


def fake_response(status_code=200, payload=None):
    body = payload if payload is not None else {}
    return type("R", (), {
        "status_code": status_code,
        "text": json.dumps(body),
        "json": staticmethod(lambda: body),
    })()


class LoadEnvTest(unittest.TestCase):
    def test_doc_key_value_bo_comment_va_dong_trong(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / ".env"
            env.write_text(
                "# comment\n\nFB_PAGE_ID=123\nFB_PAGE_ACCESS_TOKEN=\"tok\"\nFB_APP_SECRET='sec'\n",
                encoding="utf-8",
            )
            with mock.patch.dict("os.environ", {}, clear=True):
                fbtoken.load_env(env)
                self.assertEqual(Path(env).name, ".env")
                import os

                self.assertEqual(os.environ["FB_PAGE_ID"], "123")
                self.assertEqual(os.environ["FB_PAGE_ACCESS_TOKEN"], "tok")
                self.assertEqual(os.environ["FB_APP_SECRET"], "sec")

    def test_khong_ghi_de_bien_da_co(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / ".env"
            env.write_text("FB_PAGE_ID=tu-file\n", encoding="utf-8")
            import os

            with mock.patch.dict("os.environ", {"FB_PAGE_ID": "tu-shell"}, clear=True):
                fbtoken.load_env(env)
                self.assertEqual(os.environ["FB_PAGE_ID"], "tu-shell")

    def test_file_khong_ton_tai_thi_bo_qua(self):
        fbtoken.load_env(Path("/khong/co/file/.env"))


class ExchangeTest(unittest.TestCase):
    def test_doi_token_thanh_cong(self):
        captured = {}

        def fake_get(url, params=None, timeout=None):
            captured.update({"url": url, "params": params})
            return fake_response(200, {"access_token": "long", "expires_in": 5183944})

        with mock.patch("requests.get", side_effect=fake_get):
            data = fbtoken.exchange("app", "secret", "short", "v26.0")
        self.assertEqual(data["access_token"], "long")
        self.assertIn("/v26.0/oauth/access_token", captured["url"])
        self.assertEqual(captured["params"]["grant_type"], "fb_exchange_token")
        self.assertEqual(captured["params"]["fb_exchange_token"], "short")

    def test_loi_graph_thi_bao_loi(self):
        with (
            mock.patch("requests.get", return_value=fake_response(400, {"error": {"code": 190}})),
            self.assertRaises(SystemExit) as ctx,
        ):
            fbtoken.exchange("app", "secret", "short", "v26.0")
        self.assertIn("Đổi token thất bại", str(ctx.exception))

    def test_thieu_access_token_trong_response(self):
        with (
            mock.patch("requests.get", return_value=fake_response(200, {"expires_in": 1})),
            self.assertRaises(SystemExit),
        ):
            fbtoken.exchange("app", "secret", "short", "v26.0")


class ListPagesTest(unittest.TestCase):
    def test_tra_ve_danh_sach_page(self):
        pages = [{"id": "1", "name": "Page A", "access_token": "pa", "tasks": ["CREATE_CONTENT"]}]
        captured = {}

        def fake_get(url, params=None, timeout=None):
            captured.update({"url": url, "params": params})
            return fake_response(200, {"data": pages})

        with mock.patch("requests.get", side_effect=fake_get):
            result = fbtoken.list_pages("user-token", "v26.0")
        self.assertEqual(result, pages)
        self.assertIn("/me/accounts", captured["url"])
        self.assertIn("access_token", captured["params"]["fields"])

    def test_loi_thi_bao_loi(self):
        with (
            mock.patch("requests.get", return_value=fake_response(400, {"error": {"code": 190}})),
            self.assertRaises(SystemExit) as ctx,
        ):
            fbtoken.list_pages("tok", "v26.0")
        self.assertIn("me/accounts", str(ctx.exception))

    def test_khong_co_data_thi_tra_rong(self):
        with mock.patch("requests.get", return_value=fake_response(200, {})):
            self.assertEqual(fbtoken.list_pages("tok", "v26.0"), [])


class MainTest(unittest.TestCase):
    def run_main(self, argv: list[str], env: dict[str, str]) -> str:
        buffer = io.StringIO()
        with (
            mock.patch.object(sys, "argv", ["fbtoken.py", *argv]),
            mock.patch.dict("os.environ", env, clear=True),
            mock.patch.object(fbtoken, "load_env"),
            redirect_stdout(buffer),
        ):
            fbtoken.main()
        return buffer.getvalue()

    def test_list_pages_dung_token_trong_env(self):
        pages = [{"id": "1", "name": "Page A", "access_token": "pa"}]
        with mock.patch.object(fbtoken, "list_pages", return_value=pages):
            out = self.run_main(["--list-pages"], {"FB_PAGE_ACCESS_TOKEN": "tok"})
        self.assertIn("Page A", out)

    def test_list_pages_thieu_token_thi_bao_loi(self):
        with self.assertRaises(SystemExit):
            self.run_main(["--list-pages"], {})

    def test_doi_token_in_ra_va_liet_ke_page(self):
        pages = [{"id": "1", "name": "Page A", "access_token": "pa"}]
        with (
            mock.patch.object(fbtoken, "exchange",
                              return_value={"access_token": "long", "expires_in": 5183944}),
            mock.patch.object(fbtoken, "list_pages", return_value=pages),
        ):
            out = self.run_main(
                ["--short", "short"],
                {"FB_APP_ID": "app", "FB_APP_SECRET": "sec", "FB_API_VERSION": "v26.0"},
            )
        self.assertIn("long", out)
        self.assertIn("~60 ngày", out)
        self.assertIn("Page A", out)

    def test_thieu_tham_so_thi_bao_loi(self):
        with self.assertRaises(SystemExit) as ctx:
            self.run_main([], {})
        self.assertIn("FB_APP_ID", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
