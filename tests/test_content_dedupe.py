#!/usr/bin/env python3
"""Chống trùng nội dung: fingerprint + độ giống nhau, và khoá chủ đề xoay vòng."""

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import logutil  # noqa: E402
from config import Cfg  # noqa: E402
from schema import (  # noqa: E402
    content_signature,
    content_similarity,
    content_tokens,
    normalize_text,
    validate_clip,
)
from topics import configured_topics, next_topic, rotation_report, topic_usage  # noqa: E402

LIST_A = {
    "id": "clip_a",
    "topic": "Phân loại tài sản theo thu nhập mỗi tháng",
    "title": "PHÂN LOẠI TÀI SẢN",
    "items": [
        {"label": "Dưới 2 triệu", "text": "Rất nghèo khổ"},
        {"label": "2 - 5 triệu", "text": "Nghèo thiếu thốn"},
    ],
}
LIST_B = {
    "id": "clip_b",
    "topic": "Kiểu người dễ thành công",
    "title": "KIỂU NGƯỜI DỄ THÀNH CÔNG",
    "items": [
        {"label": "Làm việc", "text": "Khi người khác nghỉ"},
        {"label": "Im lặng", "text": "Để thành công lên tiếng"},
    ],
}


class NormalizeTest(unittest.TestCase):
    def test_bo_dau_va_chu_hoa(self):
        self.assertEqual(normalize_text("PHÂN LOẠI TÀI SẢN"), "phan loai tai san")
        self.assertEqual(normalize_text("Đẳng cấp!"), "dang cap")

    def test_gop_khoang_trang_va_ky_tu_la(self):
        self.assertEqual(normalize_text("  a   b \n c  "), "a b c")
        self.assertEqual(normalize_text("2 - 5 triệu"), "2 5 trieu")


class SignatureTest(unittest.TestCase):
    def test_on_dinh_voi_cung_noi_dung(self):
        first = content_signature(validate_clip(dict(LIST_A)))
        second = content_signature(validate_clip(dict(LIST_A)))
        self.assertEqual(first, second)
        self.assertEqual(len(first["fingerprint"]), 16)

    def test_khac_noi_dung_thi_khac_fingerprint(self):
        self.assertNotEqual(
            content_signature(validate_clip(dict(LIST_A)))["fingerprint"],
            content_signature(validate_clip(dict(LIST_B)))["fingerprint"],
        )

    def test_bo_dau_khong_lam_doi_fingerprint(self):
        accented = validate_clip({**LIST_A, "title": "PHÂN LOẠI TÀI SẢN"})
        plain = validate_clip({**LIST_A, "title": "PHAN LOAI TAI SAN"})
        self.assertEqual(
            content_signature(accented)["fingerprint"], content_signature(plain)["fingerprint"]
        )


class SimilarityTest(unittest.TestCase):
    def test_giong_nhau_hoan_toan(self):
        tokens = content_tokens(validate_clip(dict(LIST_A)))
        self.assertEqual(content_similarity(tokens, tokens), 1.0)

    def test_roi_nhau(self):
        left = content_tokens(validate_clip(dict(LIST_A)))
        right = content_tokens(validate_clip(dict(LIST_B)))
        self.assertEqual(content_similarity(left, right), 0.0)

    def test_gan_giong(self):
        base = validate_clip(dict(LIST_A))
        tweaked = validate_clip(
            {
                **LIST_A,
                "id": "clip_c",
                "items": [
                    {"label": "Dưới 2 triệu", "text": "Rất nghèo khổ"},
                    {"label": "2 - 5 triệu", "text": "Rất thiếu thốn"},
                    {"label": "6 - 9 triệu", "text": "Cận nghèo"},
                ],
            }
        )
        similarity = content_similarity(content_tokens(base), content_tokens(tweaked))
        self.assertGreater(similarity, 0.4)
        self.assertLess(similarity, 1.0)

    def test_tap_rong_thi_khong_coi_la_trung(self):
        self.assertEqual(content_similarity([], ["a"]), 0.0)


class DuplicateGuardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.log_file = Path(self.tmp.name) / "published.json"
        cfg = Cfg(
            {
                "paths": {"published_log": str(self.log_file)},
                "facebook": {"daily_limit": 30},
                "content": {"duplicate_similarity": 0.85},
            }
        )
        patcher = mock.patch.object(logutil, "load_config", return_value=cfg)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.cfg = cfg

    def publish(self, clip: dict, ts: float | None = None) -> None:
        data = validate_clip(dict(clip))
        logutil.record_publish(
            data["id"], "vid_" + data["id"], "PUBLISHED",
            f"https://facebook.com/reel/{data['id']}", {"topic": data.get("topic", "")}, data=data,
        )
        if ts is not None:
            raw = json.loads(self.log_file.read_text(encoding="utf-8"))
            raw["posts"][-1]["ts"] = ts
            self.log_file.write_text(json.dumps(raw), encoding="utf-8")

    def test_bai_da_dang_bi_chan(self):
        self.publish(LIST_A)
        with self.assertRaises(SystemExit) as ctx:
            logutil.assert_can_publish("clip_a", data=validate_clip(dict(LIST_A)))
        self.assertIn("đã đăng", str(ctx.exception))

    def test_bai_doi_id_nhung_cung_noi_dung_bi_chan(self):
        self.publish(LIST_A)
        same = validate_clip({**LIST_A, "id": "clip_a_copy"})
        with self.assertRaises(SystemExit) as ctx:
            logutil.assert_can_publish("clip_a_copy", data=same)
        self.assertIn("--force", str(ctx.exception))

    def test_noi_dung_khac_thi_duoc_dang(self):
        self.publish(LIST_A)
        logutil.assert_can_publish("clip_b", data=validate_clip(dict(LIST_B)))

    def test_force_bo_qua_chan_trung(self):
        self.publish(LIST_A)
        logutil.assert_can_publish("clip_a", force=True, data=validate_clip(dict(LIST_A)))

    def test_nguong_0_thi_tat_kiem_tra(self):
        self.publish(LIST_A)
        with mock.patch.object(
            logutil, "load_config",
            return_value=Cfg({**self.cfg.as_dict(), "content": {"duplicate_similarity": 0}}),
        ):
            logutil.assert_can_publish("clip_a_copy", data=validate_clip({**LIST_A, "id": "x"}))

    def test_draft_khong_chan_bai_moi(self):
        draft = validate_clip({**LIST_A, "id": "clip_draft"})
        logutil.record_publish("clip_draft", "vid_d", "DRAFT", "https://x", data=draft)
        logutil.assert_can_publish("clip_a_copy", data=validate_clip({**LIST_A, "id": "clip_a_copy"}))

    def test_log_luu_chu_ky_noi_dung(self):
        self.publish(LIST_A)
        post = json.loads(self.log_file.read_text(encoding="utf-8"))["posts"][0]
        self.assertIn("fingerprint", post["content"])
        self.assertTrue(post["content"]["tokens"])
        self.assertEqual(post["topic"], LIST_A["topic"])

    def test_duplicate_content_tra_bai_gan_nhat(self):
        self.publish(LIST_B)
        self.publish(LIST_A)
        found = logutil.duplicate_content(validate_clip(dict(LIST_A)))
        self.assertEqual(found["clip_id"], "clip_a")


class TopicRotationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.log_file = Path(self.tmp.name) / "published.json"
        self.cfg = Cfg(
            {
                "paths": {"published_log": str(self.log_file)},
                "facebook": {"daily_limit": 30},
                "content": {"topics": list(LIST_A["topic"].split("|")) + [LIST_B["topic"], "Chủ đề C"]},
            }
        )
        patcher = mock.patch.object(logutil, "load_config", return_value=self.cfg)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_doc_chu_de_tu_config(self):
        self.assertEqual(configured_topics(self.cfg), [LIST_A["topic"], LIST_B["topic"], "Chủ đề C"])

    def test_thieu_config_thi_dung_mac_dinh(self):
        self.assertTrue(configured_topics(Cfg({})))

    def test_chua_dang_gi_thi_chon_chu_de_dau(self):
        self.assertEqual(next_topic(self.cfg), LIST_A["topic"])

    def test_chon_chu_de_it_dung_va_lau_nhat(self):
        now = time.time()
        logutil.record_publish("c1", "v1", "PUBLISHED", "u1", {"topic": LIST_A["topic"]})
        raw = json.loads(self.log_file.read_text(encoding="utf-8"))
        raw["posts"][0]["ts"] = now
        self.log_file.write_text(json.dumps(raw), encoding="utf-8")
        self.assertEqual(next_topic(self.cfg), LIST_B["topic"])
        self.assertEqual(topic_usage()[LIST_A["topic"]]["count"], 1)

    def test_draft_khong_tinh_vao_xoay_vong(self):
        logutil.record_publish("c1", "v1", "DRAFT", "u1", {"topic": LIST_A["topic"]})
        self.assertEqual(topic_usage(), {})
        self.assertEqual(next_topic(self.cfg), LIST_A["topic"])

    def test_rotation_report_giu_thu_tu_config(self):
        report = rotation_report(self.cfg)
        self.assertEqual([row[0] for row in report], configured_topics(self.cfg))
        self.assertEqual(report[0][1], 0)
        self.assertEqual(report[0][2], "chưa đăng")

    def test_chu_de_de_bai_cu_quay_lai(self):
        now = time.time()
        for topic in configured_topics(self.cfg):
            logutil.record_publish("c", "v", "PUBLISHED", "u", {"topic": topic})
        raw = json.loads(self.log_file.read_text(encoding="utf-8"))
        for index, post in enumerate(raw["posts"]):
            post["ts"] = now - (10 - index) * 3600
        self.log_file.write_text(json.dumps(raw), encoding="utf-8")
        self.assertEqual(next_topic(self.cfg), configured_topics(self.cfg)[0])


if __name__ == "__main__":
    unittest.main()
