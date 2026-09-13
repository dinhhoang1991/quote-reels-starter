#!/usr/bin/env python3
"""Phụ đề: gom cue, sinh ASS, karaoke, và dịch mốc theo head_seconds."""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from subtitles import (  # noqa: E402
    DEFAULT_STYLE,
    NS_PER_MS,
    Word,
    ass_color,
    ass_document,
    ass_escape,
    ass_timestamp,
    build_cues,
    cue_text,
    ms_to_offset,
    shift_words,
    subtitles_filter,
    words_from_timings,
    write_ass,
)


def timings(*pairs) -> list[dict]:
    """Sinh WordBoundary giả: (text, start_ms, end_ms)."""
    return [
        {"text": text, "offset": ms_to_offset(start), "duration": ms_to_offset(end - start)}
        for text, start, end in pairs
    ]


class OffsetTest(unittest.TestCase):
    def test_doi_ms_sang_don_vi_100ns(self):
        self.assertEqual(ms_to_offset(1), 10_000)
        self.assertEqual(ms_to_offset(0), 0)

    def test_words_tu_wordboundary(self):
        words = words_from_timings(timings(("Xin", 100, 480), ("chào", 480, 752)))
        self.assertEqual([word.text for word in words], ["Xin", "chào"])
        self.assertEqual(words[0].start_ms, 100)
        self.assertEqual(words[1].end_ms, 752)

    def test_sap_xep_theo_thoi_gian_va_bo_tu_rong(self):
        words = words_from_timings(timings(("sau", 500, 700), ("  ", 0, 0), ("trước", 100, 300)))
        self.assertEqual([word.text for word in words], ["trước", "sau"])

    def test_danh_sach_rong(self):
        self.assertEqual(words_from_timings([]), [])

    def test_duration_0_van_hop_le(self):
        words = words_from_timings([{"text": "a", "offset": ms_to_offset(100), "duration": 0}])
        self.assertEqual(words[0].end_ms, 100)


class ShiftTest(unittest.TestCase):
    def test_dich_moc_theo_head(self):
        words = words_from_timings(timings(("a", 0, 200), ("b", 200, 400)))
        shifted = shift_words(words, 600)
        self.assertEqual(shifted[0].start_ms, 600)
        self.assertEqual(shifted[1].end_ms, 1000)

    def test_offset_0_khong_doi_gi(self):
        words = words_from_timings(timings(("a", 0, 200)))
        self.assertEqual(shift_words(words, 0)[0].start_ms, words[0].start_ms)


class BuildCuesTest(unittest.TestCase):
    def words(self, count: int, step_ms: int = 250, text: str = "từ") -> list[Word]:
        return [
            Word(text=f"{text}{index}", start_ms=index * step_ms, end_ms=index * step_ms + 200)
            for index in range(count)
        ]

    def test_gom_theo_so_tu_moi_cue(self):
        cues = build_cues(self.words(7), {"words_per_cue": 3})
        self.assertEqual([len(cue.words) for cue in cues], [3, 3, 1])

    def test_cat_o_dau_ket_cau(self):
        words = [
            Word("Một.", 0, 200), Word("Hai", 250, 400), Word("Ba", 450, 600),
        ]
        cues = build_cues(words, {"words_per_cue": 3})
        self.assertEqual(len(cues), 2)
        self.assertEqual(cues[0].text, "Một.")
        self.assertEqual(cues[1].text, "Hai Ba")

    def test_cat_khi_khoang_lang_dai(self):
        words = [Word("a", 0, 200), Word("b", 5000, 5200)]
        cues = build_cues(words, {"words_per_cue": 5, "max_gap_ms": 800})
        self.assertEqual(len(cues), 2)

    def test_cat_khi_qua_dai(self):
        words = [Word("dàidàidài", index * 100, index * 100 + 80) for index in range(4)]
        cues = build_cues(words, {"words_per_cue": 10, "max_chars_per_cue": 12})
        self.assertGreater(len(cues), 1)

    def test_end_khong_vuot_cue_sau(self):
        words = [Word("a", 0, 200), Word("b", 300, 500)]
        cues = build_cues(words, {"words_per_cue": 1, "hold_ms": 1000})
        self.assertEqual(len(cues), 2)
        self.assertLessEqual(cues[0].end_ms, cues[1].start_ms)

    def test_hold_duoc_cong_vao_cuoi(self):
        cues = build_cues([Word("a", 0, 200)], {"hold_ms": 120})
        self.assertEqual(cues[0].end_ms, 320)

    def test_danh_sach_rong(self):
        self.assertEqual(build_cues([], {}), [])


class AssTest(unittest.TestCase):
    def test_timestamp(self):
        self.assertEqual(ass_timestamp(0), "0:00:00.00")
        self.assertEqual(ass_timestamp(1234), "0:00:01.23")
        self.assertEqual(ass_timestamp(1_234_567), "0:20:34.56")
        self.assertEqual(ass_timestamp(-5), "0:00:00.00")

    def test_mau_ass(self):
        self.assertEqual(ass_color("#FFFFFF"), "&H00FFFFFF")
        self.assertEqual(ass_color("#FFE566"), "&H0066E5FF")
        self.assertEqual(ass_color("000000"), "&H00000000")

    def test_mau_sai_thi_bao_loi(self):
        with self.assertRaises(SystemExit):
            ass_color("#FFF")

    def test_escape_bo_ky_tu_dieu_khien(self):
        self.assertEqual(ass_escape("{hack} \\n"), "(hack) \\\\n")

    def test_cue_text_viet_hoa(self):
        cue = build_cues([Word("xin", 0, 200), Word("chào", 250, 400)], {"words_per_cue": 3})[0]
        self.assertEqual(cue_text(cue, {"uppercase": True}), "XIN CHÀO")
        self.assertEqual(cue_text(cue, {"uppercase": False}), "xin chào")

    def test_cue_text_karaoke_co_thoi_luong_tung_tu(self):
        cue = build_cues([Word("xin", 0, 200), Word("chào", 250, 650)], {"words_per_cue": 3})[0]
        text = cue_text(cue, {"karaoke": True, "uppercase": True})
        self.assertIn("{\\k20}XIN", text)
        self.assertIn("{\\k40}CHÀO", text)

    def test_document_co_header_style_va_dialogue(self):
        cues = build_cues([Word("xin", 0, 200)], {"words_per_cue": 3})
        doc = ass_document(cues, {"font_size": 64, "margin_v": 470}, 1080, 1920)
        self.assertIn("[Script Info]", doc)
        self.assertIn("WrapStyle: 0", doc)
        self.assertIn("PlayResX: 1080", doc)
        self.assertIn("Style: Default,Be Vietnam Pro,64,", doc)
        self.assertIn(",2,60,60,470,1", doc)  # alignment + margins
        self.assertIn("Dialogue: 0,0:00:00.00,0:00:00.32,Default", doc)

    def test_style_mac_dinh_dung_font_trong_repo(self):
        self.assertEqual(DEFAULT_STYLE["font_name"], "Be Vietnam Pro")

    def test_write_ass(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sub" / "clip.ass"
            cues = build_cues([Word("a", 0, 100)], {})
            self.assertEqual(write_ass(path, cues, {}), path)
            self.assertIn("Dialogue:", path.read_text(encoding="utf-8"))


class FilterTest(unittest.TestCase):
    def test_filter_co_file_va_fontsdir(self):
        filt = subtitles_filter(Path("/repo/assets/overlays/x.ass"), Path("/repo/assets/fonts"))
        self.assertEqual(
            filt,
            "subtitles=filename='/repo/assets/overlays/x.ass':fontsdir='/repo/assets/fonts'",
        )

    def test_duong_dan_chua_nhay_don_thi_bao_loi(self):
        with self.assertRaises(SystemExit):
            subtitles_filter(Path("/repo/it's/x.ass"), Path("/repo/assets/fonts"))

    def test_ns_per_ms_khop_edge_tts(self):
        self.assertEqual(NS_PER_MS, 10_000)


if __name__ == "__main__":
    unittest.main()
