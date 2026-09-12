#!/usr/bin/env python3
"""First comment, cover và thumb_offset — không gọi mạng."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import upload_facebook as fb  # noqa: E402
from config import Cfg, load_config  # noqa: E402
from make_video import cover_at_seconds, cover_path, make_cover  # noqa: E402
from schema import validate_clip  # noqa: E402

CLIP = {
    "id": "clip_x",
    "title": "TIÊU ĐỀ HAY",
    "topic": "Chủ đề A",
    "footer": "BẠN CHỌN SỐ NÀO?",
    "items": [{"label": "Nhãn", "text": "Vài chữ"}],
}


def config_with(facebook: dict | None = None, cover: dict | None = None) -> Cfg:
    raw = load_config().as_dict()
    if facebook is not None:
        raw["facebook"].update(facebook)
    if cover is not None:
        raw.setdefault("cover", {}).update(cover)
    return Cfg(raw)


class FirstCommentTextTest(unittest.TestCase):
    def test_clip_ghi_de_template(self):
        clip = validate_clip({**CLIP, "first_comment": "Comment riêng của clip"})
        self.assertEqual(
            fb.first_comment_text(clip, config_with({"first_comment": "từ config"})),
            "Comment riêng của clip",
        )

    def test_dung_template_config_voi_placeholder(self):
        text = fb.first_comment_text(
            validate_clip(dict(CLIP)),
            config_with({"first_comment": "{footer} — bạn chọn số mấy? #{topic}"}),
        )
        self.assertIn("BẠN CHỌN SỐ NÀO?", text)
        self.assertIn("Chủ đề A", text)

    def test_placeholder_la_khong_lam_vo(self):
        text = fb.first_comment_text(
            validate_clip(dict(CLIP)), config_with({"first_comment": "A {khong_co} B"})
        )
        self.assertEqual(text, "A B")

    def test_tat_thi_tra_rong(self):
        self.assertEqual(fb.first_comment_text(validate_clip(dict(CLIP)), config_with({"first_comment": ""})), "")
        self.assertEqual(fb.first_comment_text(None, config_with({"first_comment": ""})), "")

    def test_render_template_gop_khoang_trang(self):
        self.assertEqual(fb.render_template("  {a}   {b}  ", {"a": "x", "b": ""}), "x")


def fake_response(status_code: int = 200, payload: dict | None = None):
    """Response giả tối thiểu cho các hàm gọi Graph."""
    body = payload if payload is not None else {}
    return type("R", (), {
        "status_code": status_code,
        "headers": {},
        "text": json.dumps(body),
        "content": b"x",
        "json": staticmethod(lambda: body),
    })()


class FirstCommentPostTest(unittest.TestCase):
    def test_goi_dung_endpoint_va_payload(self):
        captured = {}

        def fake_request(method, url, retries, **kwargs):
            captured.update({"method": method, "url": url, "data": kwargs.get("data")})
            return fake_response(200, {"id": "comment_1"})

        with mock.patch.object(fb, "request_with_retry", side_effect=fake_request):
            result = fb.post_first_comment("vid1", "tok", "v26.0", "Nội dung", 3)
        self.assertEqual(result, {"id": "comment_1"})
        self.assertEqual(captured["url"], "https://graph.facebook.com/v26.0/vid1/comments")
        self.assertEqual(captured["data"]["message"], "Nội dung")
        self.assertEqual(captured["data"]["access_token"], "tok")

    def test_loi_graph_thi_raise(self):
        resp = fake_response(400, {"error": {"code": 190, "message": "x"}})
        with (
            mock.patch.object(fb, "request_with_retry", return_value=resp),
            self.assertRaises(SystemExit),
        ):
            fb.post_first_comment("vid1", "tok", "v26.0", "Nội dung", 1)


class FinishThumbTest(unittest.TestCase):
    def payload_for(self, thumb: int) -> dict:
        captured = {}

        def fake_request(method, url, retries, **kwargs):
            captured.update(kwargs.get("data") or {})
            return fake_response(200, {"success": True})

        with mock.patch.object(fb, "request_with_retry", side_effect=fake_request):
            fb.finish_publish("page", "tok", "v26.0", "vid", "desc", "title", "PUBLISHED", None, 1,
                              thumb_offset_ms=thumb)
        return captured

    def test_khong_gui_thumb_offset_khi_tat(self):
        self.assertNotIn("thumb_offset", self.payload_for(0))

    def test_gui_thumb_offset_khi_bat(self):
        self.assertEqual(self.payload_for(3200)["thumb_offset"], 3200)


class UploadReelCommentTest(unittest.TestCase):
    """upload_reel phải đăng comment đầu và không làm fail khi comment lỗi."""

    def build_reel(self, first_comment, comment_fails=False):
        cfg = config_with({"first_comment": "{footer}", "thumb_offset_ms": 1500})
        events = []

        def fake_finish(*args, **kwargs):
            events.append(("finish", kwargs.get("thumb_offset_ms")))
            return {"success": True}

        def fake_comment(video_id, token, version, message, retries):
            events.append(("comment", message))
            if comment_fails:
                raise SystemExit("Graph chặn comment")
            return {"id": "c1"}

        with (
            mock.patch.object(fb, "load_config", return_value=cfg),
            mock.patch.object(fb, "probe_duration", return_value=20.0),
            mock.patch.object(fb, "assert_can_publish"),
            mock.patch.object(fb, "record_publish"),
            mock.patch.object(fb, "remaining_quota", return_value=30),
            mock.patch.object(fb, "start_session", return_value=("vid1", "https://upload")),
            mock.patch.object(fb, "upload_binary"),
            mock.patch.object(fb, "wait_ready"),
            mock.patch.object(fb, "finish_publish", side_effect=fake_finish),
            mock.patch.object(fb, "post_first_comment", side_effect=fake_comment),
        ):
            result = fb.upload_reel(
                Path("/tmp/khong-can-that.mp4"), "page", "tok", "desc", "title",
                clip_id="clip_x", clip=validate_clip(dict(CLIP)), first_comment=first_comment,
            )
        return result, events

    def test_dang_comment_tu_template_config(self):
        result, events = self.build_reel(None)
        self.assertIn(("comment", "BẠN CHỌN SỐ NÀO?"), events)
        self.assertEqual(result["first_comment"], {"id": "c1"})
        self.assertIn(("finish", 1500), events)

    def test_truyen_chuoi_thi_dung_chuoi_do(self):
        _, events = self.build_reel("Comment tay")
        self.assertIn(("comment", "Comment tay"), events)

    def test_chuoi_rong_thi_khong_comment(self):
        result, events = self.build_reel("")
        self.assertFalse([event for event in events if event[0] == "comment"])
        self.assertNotIn("first_comment", result)

    def test_comment_loi_thi_ghi_loi_chu_khong_fail(self):
        result, _ = self.build_reel(None, comment_fails=True)
        self.assertIn("first_comment_error", result)
        self.assertIn("Graph chặn comment", result["first_comment_error"])


class CoverTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        raw = load_config().as_dict()
        raw["paths"]["overlay_dir"] = self.tmp.name
        self.cfg = Cfg(raw)
        self.data = validate_clip(dict(CLIP))

    def test_cover_path_nam_trong_overlay_dir(self):
        path = cover_path(self.data, self.cfg)
        self.assertEqual(path.name, "clip_x_cover.jpg")
        self.assertEqual(path.parent, Path(self.tmp.name))

    def test_moc_cover_mac_dinh_la_giua_doan_hook(self):
        self.assertAlmostEqual(cover_at_seconds(self.cfg), float(self.cfg.hook.seconds) / 2)

    def test_moc_cover_ghi_de_tu_config(self):
        raw = self.cfg.as_dict()
        raw["cover"] = {"at_seconds": 1.25}
        self.assertAlmostEqual(cover_at_seconds(Cfg(raw)), 1.25)

    def test_make_cover_goi_ffmpeg_dung_tham_so(self):
        with mock.patch("make_video.run") as run:
            path = make_cover(Path("/tmp/clip.mp4"), self.data, self.cfg)
        self.assertEqual(path, cover_path(self.data, self.cfg))
        cmd = run.call_args.args[0]
        self.assertEqual(cmd[0], "ffmpeg")
        self.assertIn("-ss", cmd)
        self.assertIn(str(path), cmd)

    def test_make_cover_that_voi_ffmpeg_that(self):
        """Chỉ chạy khi có ffmpeg trên PATH (CI có, máy dev có thể không)."""
        import shutil

        if shutil.which("ffmpeg") is None:
            self.skipTest("không có ffmpeg")
        video = Path(self.tmp.name) / "in.mp4"
        import subprocess

        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
             "-i", "color=c=blue:s=320x480:d=2", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video)],
            check=True,
        )
        out = make_cover(video, self.data, self.cfg)
        self.assertTrue(out.exists())
        self.assertGreater(out.stat().st_size, 100)


if __name__ == "__main__":
    unittest.main()
