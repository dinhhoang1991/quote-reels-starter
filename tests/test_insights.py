#!/usr/bin/env python3
"""Insights: lấy metric, lùi về từng metric khi Graph từ chối, xếp hạng theo chủ đề."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import insights  # noqa: E402
import logutil  # noqa: E402
from config import Cfg  # noqa: E402


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.headers = {}
        self.text = text
        self.content = b"body" if payload is not None else b""

    def json(self):
        if self._payload is None:
            raise ValueError("không phải JSON")
        return self._payload


def insight_payload(*metrics):
    return {"data": [{"name": name, "values": [{"value": 10}]} for name in metrics]}


class ParseTest(unittest.TestCase):
    def test_doi_response_thanh_dict_metric(self):
        parsed = insights.parse_insights(insight_payload("post_video_views", "blue_reels_play_count"))
        self.assertEqual(set(parsed), {"post_video_views", "blue_reels_play_count"})

    def test_bo_entry_thieu_ten(self):
        parsed = insights.parse_insights({"data": [{"values": []}, {"name": "ok", "values": []}]})
        self.assertEqual(set(parsed), {"ok"})

    def test_metric_value_doc_dang_scalar_list_va_dict(self):
        self.assertEqual(insights.metric_value([{"value": 12}]), 12.0)
        self.assertEqual(insights.metric_value([{"value": {"like": 3, "love": 2}}]), 5.0)
        self.assertEqual(insights.metric_value(7), 7.0)
        self.assertEqual(insights.metric_value(None), 0.0)


class FetchTest(unittest.TestCase):
    def test_lay_duoc_ca_lo(self):
        with mock.patch.object(
            insights, "_fetch_once", return_value=FakeResponse(200, insight_payload("a", "b"))
        ):
            values, failed = insights.fetch_video_insights("vid", "tok", "v26.0", ["a", "b"])
        self.assertEqual(set(values), {"a", "b"})
        self.assertEqual(failed, [])

    def test_graph_tu_choi_ca_lo_thi_thu_tung_metric(self):
        def fake(video_id, token, version, metrics, retries):
            if len(metrics) > 1:
                return FakeResponse(400, {"error": {"code": 100}})
            if metrics[0] == "good":
                return FakeResponse(200, insight_payload("good"))
            return FakeResponse(400, {"error": {"code": 100}})

        with mock.patch.object(insights, "_fetch_once", side_effect=fake):
            values, failed = insights.fetch_video_insights("vid", "tok", "v26.0", ["good", "bad"])
        self.assertEqual(set(values), {"good"})
        self.assertEqual(failed, ["bad"])

    def test_metric_don_le_loi_thi_ghi_vao_failed(self):
        with mock.patch.object(
            insights, "_fetch_once", return_value=FakeResponse(400, {"error": {"code": 100}})
        ):
            values, failed = insights.fetch_video_insights("vid", "tok", "v26.0", ["only"])
        self.assertEqual(values, {})
        self.assertEqual(failed, ["only"])

    def test_danh_sach_metric_rong(self):
        self.assertEqual(insights.fetch_video_insights("vid", "tok", "v26.0", []), ({}, []))


class PublishedVideosTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.log_file = Path(self.tmp.name) / "published.json"
        self.cfg = Cfg({"paths": {"published_log": str(self.log_file)}, "facebook": {"daily_limit": 30}})
        patcher = mock.patch.object(logutil, "load_config", return_value=self.cfg)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_chi_lay_bai_published_co_video_id_moi_nhat_truoc(self):
        logutil.record_publish("old", "v1", "PUBLISHED", "u1", {"topic": "T1"})
        logutil.record_publish("draft", "v2", "DRAFT", "u2", {"topic": "T2"})
        logutil.record_publish("new", "v3", "PUBLISHED", "u3", {"topic": "T3"})
        raw = json.loads(self.log_file.read_text(encoding="utf-8"))
        for index, post in enumerate(raw["posts"]):
            post["ts"] = 100 + index
        self.log_file.write_text(json.dumps(raw), encoding="utf-8")
        videos = insights.published_videos()
        self.assertEqual([video["clip_id"] for video in videos], ["new", "old"])
        self.assertEqual(videos[0]["topic"], "T3")

    def test_gioi_han_so_video(self):
        for index in range(5):
            logutil.record_publish(f"c{index}", f"v{index}", "PUBLISHED", "u", {})
        self.assertEqual(len(insights.published_videos(limit=2)), 2)


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "insights.json"
        self.cfg = Cfg(
            {"paths": {"published_log": str(Path(self.tmp.name) / "p.json"),
                       "insights_log": str(self.path)},
             "facebook": {"daily_limit": 30, "api_version": "v26.0", "max_retries": 3}}
        )
        patcher = mock.patch.object(insights, "load_config", return_value=self.cfg)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_load_khi_chua_co_file(self):
        self.assertEqual(insights.load_insights(), {"videos": {}})

    def test_load_file_hong(self):
        self.path.write_text("{hong", encoding="utf-8")
        self.assertEqual(insights.load_insights(), {"videos": {}})

    def test_save_roi_load_lai(self):
        insights.save_insights({"videos": {"v1": {"clip_id": "c1", "metrics": {"a": 1}}}})
        self.assertIn("v1", insights.load_insights()["videos"])
        self.assertFalse((self.path.parent / "insights.json.tmp").exists())

    def test_refresh_can_token(self):
        with mock.patch("insights.load_env"), mock.patch.dict("os.environ", {}, clear=True), self.assertRaises(SystemExit):
            insights.refresh()

    def test_refresh_luu_metric_va_loi(self):
        posts = {"posts": [
            {"clip_id": "c1", "video_id": "v1", "state": "PUBLISHED", "ts": 1, "topic": "T1"}
        ]}
        with mock.patch("insights.load_env"), \
                mock.patch.dict("os.environ", {"FB_PAGE_ACCESS_TOKEN": "tok"}, clear=True), \
                mock.patch.object(insights, "load_log", return_value=posts), \
                mock.patch.object(
                    insights, "fetch_video_insights", return_value=({"a": [{"value": 3}]}, ["bad"])
                ):
            data = insights.refresh()
        entry = data["videos"]["v1"]
        self.assertEqual(entry["clip_id"], "c1")
        self.assertEqual(entry["metrics_failed"], ["bad"])
        self.assertIn("fetched_at", entry)
        self.assertIn("v1", insights.load_insights()["videos"])


class SummaryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "insights.json"
        self.cfg = Cfg(
            {"paths": {"insights_log": str(self.path)},
             "insights": {"metrics": ["blue_reels_play_count"]}}
        )
        patcher = mock.patch.object(insights, "load_config", return_value=self.cfg)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_chua_co_du_lieu(self):
        self.assertIn("chưa có dữ liệu", insights.summary()[0])

    def test_xep_hang_va_tong_theo_chu_de(self):
        data = {"videos": {
            "v1": {"clip_id": "c1", "topic": "T1", "metrics": {"blue_reels_play_count": [{"value": 50}]}},
            "v2": {"clip_id": "c2", "topic": "T1", "metrics": {"blue_reels_play_count": [{"value": 10}]}},
            "v3": {"clip_id": "c3", "topic": "T2", "metrics": {"blue_reels_play_count": [{"value": 900}]}},
        }}
        lines = insights.summary(data)
        self.assertIn("blue_reels_play_count", lines[0])
        self.assertTrue(any("c3" in line for line in lines))
        self.assertTrue(any("T2" in line for line in lines[2:]))
        self.assertTrue(any("(2 clip)" in line for line in lines))

    def test_metric_thieu_thi_tinh_la_0(self):
        data = {"videos": {"v1": {"clip_id": "c1", "topic": "", "metrics": {}}}}
        lines = insights.summary(data)
        self.assertTrue(any("không rõ chủ đề" in line for line in lines))


if __name__ == "__main__":
    unittest.main()
