#!/usr/bin/env python3
"""Cross-post YouTube Shorts + TikTok: payload/metadata và luồng upload (HTTP giả)."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import logutil  # noqa: E402
import publish  # noqa: E402
import upload_tiktok as tk  # noqa: E402
import upload_youtube as yt  # noqa: E402
from config import Cfg  # noqa: E402
from schema import validate_clip  # noqa: E402

CLIP = {
    "id": "clip_x",
    "title": "PHÂN LOẠI TÀI SẢN\nTHEO THU NHẬP",
    "caption": "Caption Facebook.\nCâu hỏi cuối?",
    "items": [{"label": "Nhãn", "text": "Vài chữ"}],
}


def fake_response(status_code=200, payload=None, headers=None, text=""):
    body = payload if payload is not None else {}
    return type("R", (), {
        "status_code": status_code,
        "headers": headers or {},
        "text": text or json.dumps(body),
        "content": b"x",
        "json": staticmethod(lambda: body),
    })()


def make_video(folder: Path, size: int = 2048) -> Path:
    video = folder / "clip_x.mp4"
    video.write_bytes(b"0" * size)
    return video


class YoutubeTextTest(unittest.TestCase):
    def test_tieu_de_them_shorts_va_bo_xuong_dong(self):
        title = yt.shorts_title("PHÂN LOẠI TÀI SẢN\nTHEO THU NHẬP")
        self.assertEqual(title, "PHÂN LOẠI TÀI SẢN THEO THU NHẬP #Shorts")

    def test_khong_them_shorts_hai_lan(self):
        self.assertEqual(yt.shorts_title("abc #shorts"), "abc #shorts")

    def test_cat_theo_gioi_han(self):
        self.assertEqual(len(yt.shorts_title("x" * 200)), 100)

    def test_mo_ta_them_hashtag_va_cat(self):
        self.assertIn("#tucky", yt.shorts_description("Caption", "#tucky"))
        # đã có sẵn thì không thêm lần nữa
        self.assertEqual(yt.shorts_description("Caption #tucky", "#tucky").count("#tucky"), 1)
        self.assertEqual(len(yt.shorts_description("x" * 6000)), 5000)

    def test_metadata_dung_part_snippet_status(self):
        meta = yt.video_metadata("T", "D", ["#a", "b"], "22", "unlisted")
        self.assertEqual(meta["snippet"]["title"], "T")
        self.assertEqual(meta["snippet"]["tags"], ["a", "b"])
        self.assertEqual(meta["snippet"]["categoryId"], "22")
        self.assertEqual(meta["status"]["privacyStatus"], "unlisted")
        self.assertFalse(meta["status"]["selfDeclaredMadeForKids"])

    def test_parse_tags_bo_dau_va_dau_phay(self):
        self.assertEqual(yt.parse_tags("#a, #b c"), ["a", "b", "c"])


class YoutubeFlowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.video = make_video(Path(self.tmp.name))
        self.cfg = Cfg({
            "facebook": {"max_retries": 2, "daily_limit": 30},
            "crosspost": {"youtube_category_id": "22", "youtube_privacy": "public"},
        })
        patcher = mock.patch.object(yt, "load_config", return_value=self.cfg)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_doi_refresh_token_lay_access_token(self):
        captured = {}

        def fake_request(method, url, retries, **kwargs):
            captured.update({"url": url, "data": kwargs.get("data")})
            return fake_response(200, {"access_token": "at123"})

        with mock.patch.object(yt, "request_with_retry", side_effect=fake_request):
            token = yt.access_token("cid", "secret", "refresh", 2)
        self.assertEqual(token, "at123")
        self.assertEqual(captured["data"]["grant_type"], "refresh_token")

    def test_thieu_credential_thi_bao_loi(self):
        with self.assertRaises(SystemExit):
            yt.access_token("", "", "")

    def test_init_gui_dung_header_va_lay_location(self):
        captured = {}

        def fake_request(method, url, retries, **kwargs):
            captured.update({"method": method, "url": url, **kwargs})
            return fake_response(200, {}, headers={"Location": "https://upload/session"})

        with mock.patch.object(yt, "request_with_retry", side_effect=fake_request):
            session = yt.init_resumable_upload(self.video, "at", {"snippet": {}}, 2)
        self.assertEqual(session, "https://upload/session")
        self.assertEqual(captured["params"]["uploadType"], "resumable")
        self.assertEqual(captured["headers"]["X-Upload-Content-Length"], "2048")
        self.assertEqual(json.loads(captured["data"])["snippet"], {})

    def test_init_thieu_location_thi_bao_loi(self):
        with mock.patch.object(yt, "request_with_retry", return_value=fake_response(200, {})), self.assertRaises(SystemExit):
            yt.init_resumable_upload(self.video, "at", {}, 1)

    def test_upload_tra_ve_video_id_va_url_shorts(self):
        with mock.patch.object(yt, "request_with_retry", return_value=fake_response(200, {"id": "vid9"})):
            resource = yt.upload_bytes("https://upload/session", self.video, "at", 1)
        self.assertEqual(resource["id"], "vid9")

    def test_308_bao_file_qua_lon(self):
        with mock.patch.object(yt, "request_with_retry", return_value=fake_response(308)), self.assertRaises(SystemExit) as ctx:
            yt.upload_bytes("https://upload/session", self.video, "at", 1)
        self.assertIn("308", str(ctx.exception))

    def test_dry_run_khong_goi_mang(self):
        with mock.patch.object(yt, "request_with_retry") as request:
            result = yt.upload_clip(self.video, validate_clip(dict(CLIP)), tags="#a #b", dry_run=True)
        request.assert_not_called()
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["metadata"]["snippet"]["tags"], ["a", "b"])
        self.assertIn("#Shorts", result["metadata"]["snippet"]["title"])

    def test_upload_day_du_kem_thumbnail(self):
        calls = []

        def fake_request(method, url, retries, **kwargs):
            calls.append((method, url))
            if url == yt.TOKEN_URL:
                return fake_response(200, {"access_token": "at"})
            if kwargs.get("params", {}).get("uploadType") == "resumable":
                return fake_response(200, {}, headers={"Location": "https://upload/session"})
            if url == yt.THUMB_URL:
                return fake_response(200, {"kind": "youtube#thumbnailSetResponse"})
            return fake_response(200, {"id": "vid1"})

        env = {"client_id": "cid", "client_secret": "sec", "refresh_token": "ref"}
        with (
            mock.patch.object(yt, "request_with_retry", side_effect=fake_request),
            mock.patch.object(yt, "load_youtube_env", return_value=env),
        ):
            result = yt.upload_clip(
                self.video, validate_clip(dict(CLIP)), tags="#a", cover=self.video, privacy="public"
            )
        self.assertEqual(result["video_id"], "vid1")
        self.assertEqual(result["url"], "https://youtube.com/shorts/vid1")
        self.assertEqual(result["thumbnail"], str(self.video))
        self.assertIn(("POST", yt.THUMB_URL), calls)

    def test_thumbnail_loi_khong_lam_fail_upload(self):
        def fake_request(method, url, retries, **kwargs):
            if url == yt.TOKEN_URL:
                return fake_response(200, {"access_token": "at"})
            if kwargs.get("params", {}).get("uploadType") == "resumable":
                return fake_response(200, {}, headers={"Location": "https://upload/session"})
            if url == yt.THUMB_URL:
                return fake_response(403, {"error": {"code": 403, "message": "no"}})
            return fake_response(200, {"id": "vid1"})

        env = {"client_id": "cid", "client_secret": "sec", "refresh_token": "ref"}
        with (
            mock.patch.object(yt, "request_with_retry", side_effect=fake_request),
            mock.patch.object(yt, "load_youtube_env", return_value=env),
        ):
            result = yt.upload_clip(self.video, {}, cover=self.video)
        self.assertEqual(result["video_id"], "vid1")
        self.assertIn("thumbnail_error", result)


class TiktokPayloadTest(unittest.TestCase):
    def test_payload_mot_chunk(self):
        payload = tk.init_payload(2048, "Caption", "SELF_ONLY", 1500)
        source = payload["source_info"]
        self.assertEqual(source["source"], "FILE_UPLOAD")
        self.assertEqual(source["video_size"], 2048)
        self.assertEqual(source["chunk_size"], 2048)
        self.assertEqual(source["total_chunk_count"], 1)
        self.assertEqual(payload["post_info"]["video_cover_timestamp_ms"], 1500)
        self.assertEqual(payload["post_info"]["privacy_level"], "SELF_ONLY")

    def test_chia_chunk_khi_chunk_size_nho(self):
        source = tk.init_payload(2500, "x", chunk_size=1000)["source_info"]
        self.assertEqual(source["chunk_size"], 1000)
        self.assertEqual(source["total_chunk_count"], 3)

    def test_privacy_sai_thi_bao_loi(self):
        with self.assertRaises(SystemExit):
            tk.init_payload(100, "x", "PUBLIC")

    def test_title_cat_va_gop_khoang_trang(self):
        self.assertEqual(tk.tiktok_title("a\n\nb"), "a b")
        self.assertEqual(len(tk.tiktok_title("x" * 3000)), 2200)

    def test_size_0_van_hop_le(self):
        self.assertEqual(tk.init_payload(0, "x")["source_info"]["video_size"], 1)


class TiktokFlowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.video = make_video(Path(self.tmp.name), size=2500)
        self.cfg = Cfg({"facebook": {"max_retries": 2, "daily_limit": 30}, "crosspost": {}})
        patcher = mock.patch.object(tk, "load_config", return_value=self.cfg)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_init_doc_publish_id_va_upload_url(self):
        payload = {"error": {"code": "ok"}, "data": {"publish_id": "p1", "upload_url": "https://up"}}
        with mock.patch.object(tk, "request_with_retry", return_value=fake_response(200, payload)):
            publish_id, url = tk.init_upload({}, "tok", 1)
        self.assertEqual((publish_id, url), ("p1", "https://up"))

    def test_init_bao_loi_tu_tiktok(self):
        payload = {"error": {"code": "invalid_param", "message": "privacy_level"}}
        with mock.patch.object(tk, "request_with_retry", return_value=fake_response(200, payload)), self.assertRaises(SystemExit) as ctx:
            tk.init_upload({}, "tok", 1)
        self.assertIn("invalid_param", str(ctx.exception))

    def test_init_thieu_du_lieu_thi_bao_loi(self):
        empty = fake_response(200, {"error": {"code": "ok"}, "data": {}})
        with (
            mock.patch.object(tk, "request_with_retry", return_value=empty),
            self.assertRaises(SystemExit),
        ):
            tk.init_upload({}, "tok", 1)

    def test_upload_chunk_gui_dung_content_range(self):
        captured = {}

        def fake_request(method, url, retries, **kwargs):
            captured.update(kwargs)
            return fake_response(204)

        with mock.patch.object(tk, "request_with_retry", side_effect=fake_request):
            tk.upload_chunk("https://up", self.video, 1000, 1000, 2500, 1)
        self.assertEqual(captured["headers"]["Content-Range"], "bytes 1000-1999/2500")
        self.assertEqual(len(captured["data"]), 1000)

    def test_dry_run_khong_goi_mang(self):
        with mock.patch.object(tk, "request_with_retry") as request:
            result = tk.upload_clip(self.video, "Caption", dry_run=True)
        request.assert_not_called()
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["payload"]["source_info"]["total_chunk_count"], 1)

    def test_thieu_token_thi_bao_loi(self):
        env = {"access_token": "", "privacy_level": "SELF_ONLY"}
        with mock.patch.object(tk, "load_tiktok_env", return_value=env), self.assertRaises(SystemExit):
            tk.upload_clip(self.video, "Caption")

    def test_upload_chia_nhieu_chunk_va_doc_status(self):
        calls = []

        def fake_request(method, url, retries, **kwargs):
            calls.append((method, url, kwargs.get("headers", {}).get("Content-Range")))
            if url == tk.INIT_URL:
                return fake_response(200, {"error": {"code": "ok"},
                                           "data": {"publish_id": "p1", "upload_url": "https://up"}})
            if url == tk.STATUS_URL:
                return fake_response(200, {"data": {"status": "PROCESSING_UPLOAD"}})
            return fake_response(204)

        env = {"access_token": "tok", "privacy_level": "SELF_ONLY"}
        with (
            mock.patch.object(tk, "request_with_retry", side_effect=fake_request),
            mock.patch.object(tk, "load_tiktok_env", return_value=env),
            mock.patch.object(tk, "init_payload",
                              side_effect=lambda size, title, privacy, cover, **kw: {
                                  "post_info": {"title": title, "privacy_level": privacy},
                                  "source_info": {"source": "FILE_UPLOAD", "video_size": size,
                                                  "chunk_size": 1000, "total_chunk_count": 3},
                              }),
        ):
            result = tk.upload_clip(self.video, "Caption")
        ranges = [call[2] for call in calls if call[2]]
        self.assertEqual(ranges, ["bytes 0-999/2500", "bytes 1000-1999/2500", "bytes 2000-2499/2500"])
        self.assertEqual(result["publish_id"], "p1")
        self.assertEqual(result["status"]["status"], "PROCESSING_UPLOAD")


class PublishTargetsTest(unittest.TestCase):
    def cfg(self, targets=None) -> Cfg:
        return Cfg({"crosspost": {"targets": targets}} if targets is not None else {})

    def test_mac_dinh_khong_crosspost(self):
        self.assertEqual(publish.resolve_targets(None, self.cfg()), [])

    def test_lay_tu_config(self):
        self.assertEqual(publish.resolve_targets(None, self.cfg(["youtube"])), ["youtube"])

    def test_cli_ghi_de_config(self):
        self.assertEqual(
            publish.resolve_targets("tiktok,youtube", self.cfg(["youtube"])),
            ["tiktok", "youtube"],
        )

    def test_none_tat_crosspost(self):
        for value in ("none", "NONE", "off", ""):
            self.assertEqual(publish.resolve_targets(value, self.cfg(["youtube"])), [])

    def test_bo_trung_va_chuan_hoa(self):
        self.assertEqual(publish.resolve_targets("YouTube, youtube ,tiktok", self.cfg()), ["youtube", "tiktok"])

    def test_target_la_thi_bao_loi(self):
        with self.assertRaises(SystemExit):
            publish.resolve_targets("instagram", self.cfg())


class PublishCrosspostTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.video = make_video(Path(self.tmp.name))
        self.cfg = Cfg({
            "facebook": {"max_retries": 1, "daily_limit": 30},
            "crosspost": {"targets": []},
            "cover": {"enabled": False},
        })
        self.data = validate_clip(dict(CLIP))

    def test_crosspost_goi_dung_ham_va_ghi_log(self):
        recorded = []
        with (
            mock.patch.object(publish, "load_config", return_value=self.cfg),
            mock.patch.object(yt, "upload_clip", return_value={"video_id": "v1", "url": "https://shorts/v1"}) as yt_upload,
            mock.patch.object(tk, "upload_clip", return_value={"publish_id": "p1"}) as tk_upload,
            mock.patch.object(logutil, "record_publish", side_effect=lambda *a, **k: recorded.append(a)),
        ):
            results = publish.crosspost(self.video, self.data, ["youtube", "tiktok"], None, "Caption")
        self.assertEqual(results["youtube"]["video_id"], "v1")
        self.assertEqual(results["tiktok"]["publish_id"], "p1")
        yt_upload.assert_called_once()
        tk_upload.assert_called_once()
        states = [call[2] for call in recorded]
        self.assertEqual(states, ["CROSSPOST_YOUTUBE", "CROSSPOST_TIKTOK"])

    def test_loi_mot_nen_tang_khong_anh_huong_nen_tang_khac(self):
        with (
            mock.patch.object(publish, "load_config", return_value=self.cfg),
            mock.patch.object(yt, "upload_clip", side_effect=SystemExit("hết hạn token")),
            mock.patch.object(tk, "upload_clip", return_value={"publish_id": "p1"}),
            mock.patch.object(logutil, "record_publish"),
        ):
            results = publish.crosspost(self.video, self.data, ["youtube", "tiktok"], None, "Caption")
        self.assertIn("hết hạn token", results["youtube"]["error"])
        self.assertNotIn("error", results["tiktok"])

    def test_nen_tang_la_khong_goi_mang(self):
        with mock.patch.object(publish, "load_config", return_value=self.cfg):
            results = publish.crosspost(self.video, self.data, ["instagram"], None, "")
        self.assertIn("nền tảng lạ", results["instagram"]["error"])

    def test_make_clip_cover_tat_thi_tra_none(self):
        with mock.patch.object(publish, "load_config", return_value=self.cfg):
            self.assertIsNone(publish.make_clip_cover(self.video, self.data))

    def test_make_clip_cover_bat_thi_sinh_anh(self):
        cfg = Cfg({**self.cfg.as_dict(), "cover": {"enabled": True}})
        expected = Path(self.tmp.name) / "clip_x_cover.jpg"
        with (
            mock.patch.object(publish, "load_config", return_value=cfg),
            mock.patch("make_video.make_cover", return_value=expected) as make,
        ):
            self.assertEqual(publish.make_clip_cover(self.video, self.data), expected)
        make.assert_called_once()

    def test_make_clip_cover_loi_thi_tra_none(self):
        cfg = Cfg({**self.cfg.as_dict(), "cover": {"enabled": True}})
        with (
            mock.patch.object(publish, "load_config", return_value=cfg),
            mock.patch("make_video.make_cover", side_effect=SystemExit("ffmpeg chết")),
        ):
            self.assertIsNone(publish.make_clip_cover(self.video, self.data))


if __name__ == "__main__":
    unittest.main()
