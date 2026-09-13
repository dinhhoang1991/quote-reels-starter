#!/usr/bin/env python3
"""--dry-run: in kế hoạch request mà KHÔNG gọi mạng, không đổi hàng chờ, không cảnh báo."""

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

import config as config_module  # noqa: E402
import publish  # noqa: E402
import upload_facebook as fb  # noqa: E402
import upload_tiktok as tk  # noqa: E402
import upload_youtube as yt  # noqa: E402
from config import Cfg  # noqa: E402
from schema import validate_clip  # noqa: E402

CLIP = {
    "id": "dry_001",
    "title": "TIÊU ĐỀ KHÔ\nHAI DÒNG",
    "items": [{"label": f"Nhãn {i}", "text": f"Mô tả {i}"} for i in range(1, 4)],
    "footer": "BẠN CHỌN SỐ NÀO?",
    "caption": "Caption thử.\nBạn chọn số nào?",
    "first_comment": "Bạn ở mức nào?",
}


def config_with(overrides: dict) -> Cfg:
    """Config thật + ghi đè, merge 1 cấp để không mất khoá con (max_retries...)."""
    raw = config_module.load_config().as_dict()
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(raw.get(key), dict):
            raw[key] = {**raw[key], **value}
        else:
            raw[key] = value
    return Cfg(raw)


class NoNetwork:
    """Chặn mọi request: dry-run mà gọi mạng là test đỏ ngay."""

    def __enter__(self):
        self.patchers = [
            mock.patch("requests.request",
                       side_effect=AssertionError("dry-run gọi requests.request")),
            mock.patch("requests.post", side_effect=AssertionError("dry-run gọi requests.post")),
            mock.patch("requests.get", side_effect=AssertionError("dry-run gọi requests.get")),
        ]
        for patcher in self.patchers:
            patcher.start()
        return self

    def __exit__(self, *exc):
        for patcher in self.patchers:
            patcher.stop()
        return False


class FacebookDryRunTest(unittest.TestCase):
    def setUp(self):
        self.cfg = config_with({
            "facebook": {"thumb_offset_ms": 1500, "daily_limit": 30,
                         "first_comment": "{footer}"},
        })

    def dry_run(self, **kwargs):
        params = {
            "clip_id": "dry_001",
            "clip": validate_clip(dict(CLIP)),
            "dry_run": True,
        }
        params.update(kwargs)
        page_id = params.pop("page_id", "page_1")
        token = params.pop("token", "token_1")
        state = params.pop("state", "PUBLISHED")
        with (
            mock.patch.object(fb, "load_config", return_value=self.cfg),
            mock.patch.object(fb, "probe_duration", return_value=12.5),
            mock.patch.object(fb, "remaining_quota", return_value=27),
            NoNetwork(),
        ):
            return fb.upload_reel(
                ROOT / "data" / "samples" / "clip_001.json", page_id, token,
                "Caption thử", "Tiêu đề", state, "v26.0", **params,
            )

    def test_ke_hoach_khong_goi_mang(self):
        plan = self.dry_run()
        self.assertTrue(plan["dry_run"])
        self.assertEqual(plan["target"], "facebook")
        self.assertIn("/page_1/video_reels", plan["endpoint"])
        self.assertEqual(plan["state"], "PUBLISHED")
        self.assertEqual(plan["duration"], 12.5)
        self.assertEqual(plan["thumb_offset_ms"], 1500)
        self.assertEqual(plan["quota_remaining"], 27)
        self.assertEqual(plan["phases"], ["start", "upload", "wait_ready", "finish"])
        self.assertTrue(plan["has_token"])

    def test_ke_hoach_uu_tien_comment_trong_clip(self):
        self.assertEqual(self.dry_run()["first_comment"], "Bạn ở mức nào?")

    def test_ke_hoach_dung_template_config_khi_clip_khong_co(self):
        clip = {key: value for key, value in CLIP.items() if key != "first_comment"}
        plan = self.dry_run(clip=validate_clip(clip))
        self.assertEqual(plan["first_comment"], "BẠN CHỌN SỐ NÀO?")

    def test_tat_comment_dau_thi_ke_hoach_rong(self):
        self.assertEqual(self.dry_run(first_comment="")["first_comment"], "")

    def test_state_khac_published_thi_khong_comment(self):
        self.assertEqual(self.dry_run(state="DRAFT")["first_comment"], "")

    def test_van_chan_clip_da_dang(self):
        """Diễn tập phải phản ánh đúng bài kiểm chống trùng của lần chạy thật."""
        with (
            mock.patch.object(fb, "load_config", return_value=self.cfg),
            mock.patch.object(fb, "probe_duration", return_value=12.5),
            mock.patch.object(fb, "assert_can_publish",
                              side_effect=SystemExit("Clip dry_001 đã đăng")),
            NoNetwork(),self.assertRaises(SystemExit)
        ):
            fb.upload_reel(Path("x.mp4"), "page_1", "token_1", "c", "t",
                           clip_id="dry_001", dry_run=True)

    def test_duration_ngoai_khoang_thi_bao_loi(self):
        with (
            mock.patch.object(fb, "load_config", return_value=self.cfg),
            mock.patch.object(fb, "probe_duration", return_value=200.0),
            NoNetwork(),self.assertRaises(SystemExit)
        ):
            fb.upload_reel(Path("x.mp4"), "page_1", "token_1", "c", "t", dry_run=True)

    def test_khong_co_credential_van_dien_tap_duoc(self):
        plan = self.dry_run(page_id="", token="", clip_id="")
        self.assertFalse(plan["has_page_id"])
        self.assertFalse(plan["has_token"])


class CrosspostDryRunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.video = Path(self.tmp.name) / "clip.mp4"
        self.video.write_bytes(b"x" * 2048)
        self.cfg = config_with({"crosspost": {"targets": ["youtube", "tiktok"]},
                                "cover": {"enabled": True, "at_seconds": 0}})

    def test_youtube_ke_hoach_khong_can_token(self):
        with (
            mock.patch.object(yt, "load_config", return_value=self.cfg),
            NoNetwork(),
        ):
            plan = yt.upload_clip(self.video, validate_clip(dict(CLIP)), caption="c",
                                  clip_id="dry_001", dry_run=True)
        self.assertTrue(plan["dry_run"])
        self.assertEqual(plan["video_bytes"], 2048)
        self.assertIn("snippet", plan["metadata"])

    def test_tiktok_ke_hoach_khong_can_token(self):
        with (
            mock.patch.object(tk, "load_config", return_value=self.cfg),
            NoNetwork(),
        ):
            plan = tk.upload_clip(self.video, "caption", clip_id="dry_001", dry_run=True)
        self.assertTrue(plan["dry_run"])
        self.assertEqual(plan["video_bytes"], 2048)
        self.assertEqual(plan["payload"]["source_info"]["video_size"], 2048)


class PublishDryRunTest(unittest.TestCase):
    """publish.py --dry-run: có kế hoạch Facebook + cross-post, không đổi hàng chờ."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.sim = Path(self.tmp.name)
        raw = config_module.load_config().as_dict()
        raw["paths"].update({
            "queue_dir": str(self.sim / "queue"),
            "published_log": str(self.sim / "published.json"),
            "voice_dir": str(self.sim / "voice"),
            "overlay_dir": str(self.sim / "overlays"),
            "out_dir": str(self.sim / "out"),
        })
        raw["crosspost"] = {**raw.get("crosspost", {}), "targets": ["youtube"]}
        self.cfg = Cfg(raw)
        self.clip = self.sim / "dry_001.json"
        self.clip.write_text(json.dumps(CLIP, ensure_ascii=False), encoding="utf-8")
        for name in ("queue/pending", "queue/done", "queue/failed",
                     "voice", "overlays", "out"):
            (self.sim / name).mkdir(parents=True, exist_ok=True)
        self.pending = self.sim / "queue" / "pending" / "dry_001.json"
        self.pending.write_text(json.dumps(CLIP, ensure_ascii=False), encoding="utf-8")

    def run_dry(self) -> str:
        buffer = io.StringIO()
        with (
            mock.patch.object(publish, "load_config", return_value=self.cfg),
            mock.patch.object(publish, "build",
                              return_value=self.sim / "out" / "v.mp4"),
            mock.patch.object(publish, "make_clip_cover", return_value=None),
            mock.patch.object(publish, "remaining_quota", return_value=30),
            mock.patch.object(fb, "probe_duration", return_value=12.0),
            mock.patch.object(fb, "load_config", return_value=self.cfg),
            mock.patch.object(fb, "remaining_quota", return_value=30),
            mock.patch.object(fb, "assert_can_publish"),
            mock.patch.object(publish, "notify_publish_success"),
            mock.patch.object(publish, "notify_publish_failure"),
            mock.patch.object(publish, "notify"),
            NoNetwork(),
            redirect_stdout(buffer),mock.patch.dict("os.environ",
                             {"FB_PAGE_ID": "", "FB_PAGE_ACCESS_TOKEN": ""})
        ):
            publish.publish_one(
                self.clip, None, None, None, "PUBLISHED", False, False,
                crosspost_targets=["youtube"], dry_run=True,
            )
        return buffer.getvalue()

    def test_in_ke_hoach_fb_va_crosspost(self):
        out = self.run_dry()
        plan = json.loads(out[out.index("{"):])
        self.assertTrue(plan["dry_run"])
        self.assertEqual(plan["clip"], "dry_001")
        self.assertTrue(plan["facebook"]["dry_run"])
        self.assertIn("youtube", plan["crosspost"])
        self.assertTrue(plan["crosspost"]["youtube"]["dry_run"])

    def test_dry_run_khong_doi_hang_cho(self):
        self.run_dry()
        self.assertTrue(self.pending.exists(), "job phải còn nguyên trong pending/")
        self.assertFalse(list((self.sim / "queue" / "done").glob("*.json")))
        self.assertFalse(list((self.sim / "queue" / "failed").glob("*.json")))

    def test_dry_run_loi_thi_khong_gui_canh_bao(self):
        with (
            mock.patch.object(publish, "load_config", return_value=self.cfg),
            mock.patch.object(publish, "build", side_effect=RuntimeError("ffmpeg hỏng")),
            mock.patch.object(publish, "notify_publish_failure") as notify_fail,
            mock.patch.object(publish, "queue_fail") as queue_fail,
        ):
            args = mock.Mock(dry_run=True, footage="", music="", voice="", state="PUBLISHED",
                             skip_upload=False, force=False, no_first_comment=False)
            ok, error = publish.publish_clip(self.clip, args, [], from_queue=True)
        self.assertFalse(ok)
        self.assertIn("ffmpeg hỏng", error)
        notify_fail.assert_not_called()
        queue_fail.assert_not_called()

    def test_batch_dry_run_khong_lap_vo_tan(self):
        """--max 0 + dry-run: job không bị chuyển đi nên phải tự giới hạn 1 clip."""
        args = mock.Mock(dry_run=True, max=0, footage="", music="", voice="",
                         state="PUBLISHED", skip_upload=False, force=False,
                         no_first_comment=False)
        calls = {"n": 0}

        def pending():
            calls["n"] += 1
            return self.pending

        with (
            mock.patch.object(publish, "load_config", return_value=self.cfg),
            # Hàng chờ do test quyết định: không phụ thuộc data/queue/pending của repo
            # (checkout sạch của CI để trống nên next_pending() thật sẽ báo "trống").
            mock.patch.object(publish, "next_pending", side_effect=pending),
            mock.patch.object(publish, "queue_move") as moved,
            mock.patch.object(publish, "queue_fail") as failed,
            mock.patch.object(publish, "build",
                              return_value=self.sim / "out" / "v.mp4"),
            mock.patch.object(publish, "make_clip_cover", return_value=None),
            mock.patch.object(publish, "remaining_quota", return_value=30),
            mock.patch.object(fb, "probe_duration", return_value=12.0),
            mock.patch.object(fb, "load_config", return_value=self.cfg),
            mock.patch.object(fb, "remaining_quota", return_value=30),
            mock.patch.object(fb, "assert_can_publish"),
            mock.patch.object(publish, "notify_empty_queue"),
            NoNetwork(),
        ):
            code = publish.run_batch(args, [], self.cfg)
        self.assertEqual(code, 0)
        self.assertEqual(calls["n"], 1, "chỉ lấy 1 clip, không lấy lại chính nó")
        moved.assert_not_called()
        failed.assert_not_called()
        self.assertTrue(self.pending.exists())


if __name__ == "__main__":
    unittest.main()
