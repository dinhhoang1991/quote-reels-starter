#!/usr/bin/env python3
"""P4.4/P4.5: điểm insights cho vòng xoay chủ đề, first_comment, caption nền tảng, batch."""

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import insights  # noqa: E402
import logutil  # noqa: E402
import topics  # noqa: E402
import upload_tiktok as tk  # noqa: E402
import upload_youtube as yt  # noqa: E402
from config import Cfg  # noqa: E402
from schema import compose_caption, content_warnings, validate_clip  # noqa: E402

TOPIC_A = "Chủ đề A"
TOPIC_B = "Chủ đề B"
TOPIC_C = "Chủ đề C"


def cfg_with(**sections) -> Cfg:
    raw = {"facebook": {"daily_limit": 30, "max_retries": 1},
           "crosspost": {}, "paths": {}}
    for key, value in sections.items():
        raw.setdefault(key, {}).update(value) if isinstance(value, dict) else raw.update({key: value})
    return Cfg(raw)


class TopicScoreTest(unittest.TestCase):
    def test_diem_trung_binh_theo_chu_de(self):
        data = {"videos": {
            "v1": {"topic": TOPIC_A, "metrics": {"blue_reels_play_count": [{"value": 100}]}},
            "v2": {"topic": TOPIC_A, "metrics": {"blue_reels_play_count": [{"value": 200}]}},
            "v3": {"topic": TOPIC_B, "metrics": {"blue_reels_play_count": [{"value": 50}]}},
        }}
        with mock.patch.object(insights, "configured_metrics", return_value=["blue_reels_play_count"]):
            scores = insights.topic_scores(data)
        self.assertEqual(scores[TOPIC_A], 150.0)
        self.assertEqual(scores[TOPIC_B], 50.0)

    def test_bo_video_chua_co_metric_thay_vi_tinh_0(self):
        data = {"videos": {
            "v1": {"topic": TOPIC_A, "metrics": {"blue_reels_play_count": [{"value": 90}]}},
            "v2": {"topic": TOPIC_A, "metrics": {}},
        }}
        with mock.patch.object(insights, "configured_metrics", return_value=["blue_reels_play_count"]):
            scores = insights.topic_scores(data)
        self.assertEqual(scores[TOPIC_A], 90.0)

    def test_bo_qua_video_khong_co_chu_de(self):
        data = {"videos": {"v1": {"topic": "", "metrics": {"blue_reels_play_count": [{"value": 5}]}}}}
        with mock.patch.object(insights, "configured_metrics", return_value=["blue_reels_play_count"]):
            self.assertEqual(insights.topic_scores(data), {})

    def test_chua_co_du_lieu(self):
        with mock.patch.object(insights, "configured_metrics", return_value=["blue_reels_play_count"]):
            self.assertEqual(insights.topic_scores({"videos": {}}), {})


class RotationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.log = Path(self.tmp.name) / "published.json"
        self.insights = Path(self.tmp.name) / "insights.json"
        self.cfg = Cfg({
            "paths": {"published_log": str(self.log), "insights_log": str(self.insights),
                      "queue_dir": str(Path(self.tmp.name) / "queue")},
            "facebook": {"daily_limit": 30},
            "content": {"topics": [TOPIC_A, TOPIC_B, TOPIC_C]},
        })
        # cả logutil (published log) và insights (insights log) phải trỏ về thư mục tạm
        for module in (logutil, insights):
            patcher = mock.patch.object(module, "load_config", return_value=self.cfg)
            patcher.start()
            self.addCleanup(patcher.stop)

    def publish(self, topic: str, ts: float | None = None) -> None:
        logutil.record_publish(f"c{topic}{time.time()}", "v", "PUBLISHED", "u", {"topic": topic})
        if ts is not None:
            raw = json.loads(self.log.read_text(encoding="utf-8"))
            raw["posts"][-1]["ts"] = ts
            self.log.write_text(json.dumps(raw), encoding="utf-8")

    def write_insights(self, scores: dict[str, float]) -> None:
        videos = {
            f"v{index}": {"topic": topic, "metrics": {"blue_reels_play_count": [{"value": score}]}}
            for index, (topic, score) in enumerate(scores.items())
        }
        self.insights.write_text(json.dumps({"videos": videos}), encoding="utf-8")

    def test_chua_dung_gi_thi_uutien_chu_de_dau(self):
        self.assertEqual(topics.next_topic(self.cfg), TOPIC_A)

    def test_cung_so_lan_thi_chon_diem_cao_hon(self):
        """Cùng số lần dùng (đã phủ đều) thì chủ đề điểm cao hơn được làm trước."""
        now = time.time()
        self.publish(TOPIC_A, ts=now - 3600)
        self.publish(TOPIC_B, ts=now - 7200)
        self.publish(TOPIC_C, ts=now - 1800)
        self.write_insights({TOPIC_A: 10, TOPIC_B: 900, TOPIC_C: 5})
        self.assertEqual(topics.next_topic(self.cfg), TOPIC_B)

    def test_phu_de_van_thang_diem_cao(self):
        """Chủ đề chưa dùng luôn được ưu tiên, dù chủ đề đã dùng có điểm rất cao."""
        now = time.time()
        self.publish(TOPIC_A, ts=now - 3600)
        self.publish(TOPIC_B, ts=now - 7200)
        self.write_insights({TOPIC_A: 99999, TOPIC_B: 99999})
        self.assertEqual(topics.next_topic(self.cfg), TOPIC_C)

    def test_cung_so_lan_cung_diem_thi_chon_lau_nhat(self):
        now = time.time()
        self.publish(TOPIC_A, ts=now - 7200)
        self.publish(TOPIC_B, ts=now - 3600)
        self.publish(TOPIC_C, ts=now - 1800)
        self.write_insights({TOPIC_A: 100, TOPIC_B: 100, TOPIC_C: 100})
        self.assertEqual(topics.next_topic(self.cfg), TOPIC_A)

    def test_rotation_report_co_diem(self):
        self.write_insights({TOPIC_A: 42})
        report = topics.rotation_report(self.cfg)
        self.assertEqual([row[0] for row in report], [TOPIC_A, TOPIC_B, TOPIC_C])
        self.assertEqual(report[0][3], 42.0)
        self.assertEqual(report[1][3], 0.0)

    def test_insights_hong_thi_khong_vo(self):
        self.insights.write_text("{hong", encoding="utf-8")
        self.assertEqual(topics.topic_scores(self.cfg), {})
        self.assertEqual(topics.next_topic(self.cfg), TOPIC_A)


class FirstCommentTest(unittest.TestCase):
    def test_chuan_hoa_va_giu_nguyen_khi_thieu(self):
        data = validate_clip({"id": "c", "title": "T", "items": [{"label": "a", "text": "b"}],
                              "first_comment": "  Bạn chọn số mấy?  "})
        self.assertEqual(data["first_comment"], "Bạn chọn số mấy?")
        plain = validate_clip({"id": "c", "title": "T", "items": [{"label": "a", "text": "b"}]})
        self.assertEqual(plain["first_comment"], "")

    def test_canh_bao_khi_qua_dai(self):
        data = validate_clip({"id": "c", "title": "T", "items": [{"label": "a", "text": "b"}] * 8,
                              "first_comment": "x" * 250})
        self.assertTrue(any("first_comment" in warning for warning in content_warnings(data)))

    def test_prompt_co_yeu_cau_first_comment(self):
        prompt = (ROOT / "prompts" / "generate_list.md").read_text(encoding="utf-8")
        for field in ("first_comment", "tiktok_caption", "youtube_caption"):
            self.assertIn(field, prompt, f"prompt thiếu {field}")


class ComposeCaptionTest(unittest.TestCase):
    def test_ghep_hashtag(self):
        self.assertEqual(compose_caption("Nội dung", "#tag"), "Nội dung\n\n#tag")

    def test_khong_ghep_trung(self):
        self.assertEqual(compose_caption("Nội dung #tag", "#tag"), "Nội dung #tag")

    def test_rong_thi_tra_nguyen_ban(self):
        self.assertEqual(compose_caption("Nội dung", ""), "Nội dung")
        self.assertEqual(compose_caption("", "#tag"), "#tag")


class PlatformCaptionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.video = Path(self.tmp.name) / "clip.mp4"
        self.video.write_bytes(b"0" * 1024)
        self.cfg = cfg_with(crosspost={"youtube_tags": "#shorts-extra", "tiktok_tags": "#tiktok-tag",
                                       "youtube_category_id": "22"},
                            paths={"voice_dir": self.tmp.name})

    def test_youtube_dung_caption_rieng_va_tag_rieng(self):
        data = validate_clip({"id": "c", "title": "TIÊU ĐỀ", "caption": "Caption Facebook #chung",
                              "youtube_caption": "Mô tả riêng cho YouTube",
                              "items": [{"label": "a", "text": "b"}]})
        with mock.patch.object(yt, "load_config", return_value=self.cfg):
            result = yt.upload_clip(self.video, data, caption="Caption Facebook #chung",
                                    tags="#chung", dry_run=True)
        description = result["metadata"]["snippet"]["description"]
        self.assertIn("Mô tả riêng cho YouTube", description)
        self.assertNotIn("Caption Facebook", description)
        self.assertIn("#shorts-extra", description)
        self.assertIn("shorts-extra", result["metadata"]["snippet"]["tags"])
        self.assertIn("chung", result["metadata"]["snippet"]["tags"])
        self.assertIn("#Shorts", result["metadata"]["snippet"]["title"])

    def test_youtube_thieu_caption_rieng_thi_dung_caption_chung(self):
        with mock.patch.object(yt, "load_config", return_value=self.cfg):
            result = yt.upload_clip(self.video, {}, caption="Caption chung", tags="", dry_run=True)
        self.assertIn("Caption chung", result["metadata"]["snippet"]["description"])
        self.assertIn("#shorts-extra", result["metadata"]["snippet"]["description"])

    def test_tiktok_caption_rieng_va_tag_rieng(self):
        env = {"access_token": "t", "privacy_level": "SELF_ONLY"}
        with (
            mock.patch.object(tk, "load_config", return_value=self.cfg),
            mock.patch.object(tk, "load_tiktok_env", return_value=env),
            mock.patch.object(tk, "init_upload", return_value=("p1", "https://up")) as init,
            mock.patch.object(tk, "upload_chunk"),
            mock.patch.object(tk, "publish_status", return_value={}),
        ):
            tk.upload_clip(self.video, "Caption riêng TikTok")
        payload = init.call_args.args[0]
        self.assertIn("Caption riêng TikTok", payload["post_info"]["title"])
        self.assertIn("#tiktok-tag", payload["post_info"]["title"])

    def test_tiktok_caption_bi_cat_theo_gioi_han(self):
        payload = tk.init_payload(10, "x" * 3000, "SELF_ONLY")
        self.assertEqual(len(payload["post_info"]["title"]), 2200)


if __name__ == "__main__":
    unittest.main()
