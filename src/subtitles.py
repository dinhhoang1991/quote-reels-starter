#!/usr/bin/env python3
"""Phụ đề burn-in: gom timing từng từ thành cue và sinh file ASS cho libass.

Đầu vào là `WordBoundary` của edge-tts (đơn vị 100 ns). Đầu ra là file .ass được
nhúng vào video bằng filter `subtitles` của ffmpeg.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

NS_PER_MS = 10_000
SENTENCE_END = ".!?…:;"
DEFAULT_STYLE: dict[str, object] = {
    "font_name": "Be Vietnam Pro",
    "font_size": 64,
    "primary_color": "#FFFFFF",
    "highlight_color": "#FFE566",
    "outline_color": "#000000",
    "outline": 4,
    "shadow": 0,
    "bold": True,
    "uppercase": True,
    "karaoke": False,
    "words_per_cue": 3,
    "max_chars_per_cue": 30,
    "max_gap_ms": 800,
    "hold_ms": 120,
    "margin_v": 420,
    "margin_h": 60,
    "alignment": 2,
}


@dataclass
class Word:
    """Một từ đã có mốc thời gian.

    @param text từ
    @param start_ms mốc bắt đầu (ms)
    @param end_ms mốc kết thúc (ms)
    """

    text: str
    start_ms: int
    end_ms: int


@dataclass
class Cue:
    """Một dòng phụ đề gồm vài từ.

    @param words các từ trong cue
    @param start_ms mốc bắt đầu
    @param end_ms mốc kết thúc (đã cộng hold và kẹp theo cue sau)
    """

    words: list[Word] = field(default_factory=list)
    start_ms: int = 0
    end_ms: int = 0

    @property
    def text(self) -> str:
        return " ".join(word.text for word in self.words).strip()


def ms_to_offset(milliseconds: int) -> int:
    """Đổi mili giây sang đơn vị 100 ns mà edge-tts dùng trong WordBoundary.

    @param milliseconds số ms
    @returns offset theo đơn vị 100 ns
    """
    return int(milliseconds) * NS_PER_MS


def words_from_timings(timings: list[dict]) -> list[Word]:
    """Đổi `WordBoundary` của edge-tts thành danh sách Word theo ms.

    @param timings các chunk WordBoundary ({text, offset, duration} đơn vị 100 ns)
    @returns danh sách Word đã sắp theo thời gian, bỏ từ rỗng
    """
    words: list[Word] = []
    for item in timings or []:
        text = str(item.get("text", "")).strip()
        if not text:
            continue
        start = int(int(item.get("offset", 0)) / NS_PER_MS)
        end = int((int(item.get("offset", 0)) + int(item.get("duration", 0))) / NS_PER_MS)
        words.append(Word(text=text, start_ms=start, end_ms=max(end, start)))
    words.sort(key=lambda word: word.start_ms)
    return words


def shift_words(words: list[Word], offset_ms: int) -> list[Word]:
    """Dịch mốc thời gian của các từ.

    Timing của edge-tts tính từ đầu file voice, còn trong mix voice bị `adelay` đẩy trễ
    `audio.head_seconds` — không dịch thì phụ đề chạy sớm hơn tiếng đúng bằng phần đó.

    @param words danh sách Word
    @param offset_ms số ms cần cộng
    @returns danh sách Word mới
    """
    if offset_ms == 0:
        return list(words)
    return [
        Word(text=word.text, start_ms=word.start_ms + offset_ms, end_ms=word.end_ms + offset_ms)
        for word in words
    ]


def build_cues(words: list[Word], style: dict | None = None) -> list[Cue]:
    """Gom từ thành cue theo số từ, số ký tự, khoảng lặng và dấu kết câu.

    @param words danh sách Word theo thời gian
    @param style các tuỳ chọn (words_per_cue, max_chars_per_cue, max_gap_ms, hold_ms, uppercase)
    @returns danh sách Cue, đã kẹp end_ms không vượt cue sau
    """
    merged = {**DEFAULT_STYLE, **(style or {})}
    per_cue = max(int(merged["words_per_cue"]), 1)
    max_chars = max(int(merged["max_chars_per_cue"]), 4)
    max_gap = int(merged["max_gap_ms"])
    hold = int(merged["hold_ms"])

    cues: list[Cue] = []
    current: list[Word] = []
    for word in words:
        if current:
            pending = " ".join(item.text for item in current) + " " + word.text
            previous = current[-1]
            too_many = len(current) >= per_cue
            too_long = len(pending) > max_chars
            long_gap = word.start_ms - previous.end_ms > max_gap
            sentence_done = previous.text.rstrip().endswith(tuple(SENTENCE_END))
            if too_many or too_long or long_gap or sentence_done:
                cues.append(Cue(words=current, start_ms=current[0].start_ms,
                                end_ms=current[-1].end_ms + hold))
                current = []
        current.append(word)
    if current:
        cues.append(Cue(words=current, start_ms=current[0].start_ms,
                        end_ms=current[-1].end_ms + hold))

    for index, cue in enumerate(cues[:-1]):
        cue.end_ms = min(cue.end_ms, cues[index + 1].start_ms)
    for cue in cues:
        cue.end_ms = max(cue.end_ms, cue.start_ms + 1)
    return cues


def ass_timestamp(milliseconds: int) -> str:
    """Đổi ms sang mốc thời gian của ASS `H:MM:SS.cc`.

    @param milliseconds số mili giây (âm sẽ thành 0)
    @returns chuỗi thời gian
    """
    total = max(int(milliseconds), 0)
    hours, remainder = divmod(total, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f"{hours}:{minutes:02d}:{seconds:02d}.{millis // 10:02d}"


def ass_color(hex_color: str) -> str:
    """Đổi `#RRGGBB` sang `&HAABBGGRR` của ASS (alpha 00 = đục).

    @param hex_color mã màu
    @returns chuỗi màu ASS
    """
    value = str(hex_color).lstrip("#")
    if len(value) != 6:
        raise SystemExit(f"màu phải dạng #RRGGBB, đang là {hex_color!r}")
    red, green, blue = value[0:2], value[2:4], value[4:6]
    return f"&H00{blue}{green}{red}".upper()


def ass_escape(text: str) -> str:
    """Bỏ ký tự điều khiển của ASS (`{`, `}`, `\\`) khỏi nội dung người dùng.

    @param text nội dung
    @returns nội dung an toàn để đặt trong Dialogue
    """
    return (
        str(text)
        .replace("\\", "\\\\")
        .replace("{", "(")
        .replace("}", ")")
        .replace("\n", " ")
        .strip()
    )


def cue_text(cue: Cue, style: dict) -> str:
    """Soạn nội dung 1 cue, có tag karaoke nếu bật.

    Khi bật karaoke, từ đã đọc được tô `primary_color` dần theo thời gian của từng từ;
    khi tắt, cả cue dùng một màu duy nhất.

    @param cue cue cần soạn
    @param style tuỳ chọn (uppercase, karaoke)
    @returns chuỗi đưa vào Dialogue
    """
    text = cue.text.upper() if style.get("uppercase", True) else cue.text
    text = ass_escape(text)
    if not style.get("karaoke"):
        return text
    parts = []
    for word in cue.words:
        duration_cs = max(int(round((word.end_ms - word.start_ms) / 10)), 1)
        token = ass_escape(word.text.upper() if style.get("uppercase", True) else word.text)
        parts.append(f"{{\\k{duration_cs}}}{token}")
    return " ".join(parts)


def ass_style_line(style: dict, width: int, height: int) -> str:
    """Dòng Style của ASS từ config.

    @param style tuỳ chọn phụ đề
    @param width chiều rộng video (chỉ để ghi chú, không dùng trong Style)
    @param height chiều cao video (dùng cho margin mặc định)
    @returns dòng `Style: ...`
    """
    merged = {**DEFAULT_STYLE, **(style or {})}
    font_size = int(merged["font_size"])
    margin_v = int(merged["margin_v"]) if merged.get("margin_v") else int(height * 0.22)
    margin_h = int(merged["margin_h"])
    bold = -1 if merged.get("bold", True) else 0
    fields = [
        "Default",
        str(merged["font_name"]),
        str(font_size),
        ass_color(str(merged["primary_color"])),
        ass_color(str(merged["highlight_color"])),
        ass_color(str(merged["outline_color"])),
        "&H00000000",
        str(bold),
        "0",
        "0",
        "0",
        "100",
        "100",
        "0",
        "0",
        "1",
        str(int(merged["outline"])),
        str(int(merged["shadow"])),
        str(int(merged["alignment"])),
        str(margin_h),
        str(margin_h),
        str(margin_v),
        "1",
    ]
    return "Style: " + ",".join(fields)


def ass_document(
    cues: list[Cue], style: dict | None = None, width: int = 1080, height: int = 1920
) -> str:
    """Sinh nội dung file ASS hoàn chỉnh.

    @param cues danh sách cue
    @param style tuỳ chọn phụ đề
    @param width chiều rộng video
    @param height chiều cao video
    @returns nội dung .ass
    """
    merged = {**DEFAULT_STYLE, **(style or {})}
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {int(width)}",
        f"PlayResY: {int(height)}",
        "WrapStyle: 0",  # tự xuống dòng nếu cue dài hơn bề ngang
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour,"
        " BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle,"
        " BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        ass_style_line(merged, width, height),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for cue in cues:
        lines.append(
            f"Dialogue: 0,{ass_timestamp(cue.start_ms)},{ass_timestamp(cue.end_ms)},"
            f"Default,,0,0,0,,{cue_text(cue, merged)}"
        )
    return "\n".join(lines) + "\n"


def write_ass(
    path: Path, cues: list[Cue], style: dict | None = None, width: int = 1080, height: int = 1920
) -> Path:
    """Ghi file ASS.

    @param path đường dẫn file .ass
    @param cues danh sách cue
    @param style tuỳ chọn phụ đề
    @param width chiều rộng video
    @param height chiều cao video
    @returns chính đường dẫn đó
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(ass_document(cues, style, width, height), encoding="utf-8")
    return path


def subtitles_filter(ass_path: Path, fonts_dir: Path) -> str:
    """Chuỗi filter `subtitles` để nhúng file ASS.

    @param ass_path file .ass
    @param fonts_dir thư mục font cho libass
    @returns đoạn filter, chưa có dấu phẩy đầu
    :raises SystemExit nếu đường dẫn chứa ký tự làm vỡ filtergraph
    """
    for value in (ass_path, fonts_dir):
        if "'" in str(value):
            raise SystemExit(f"đường dẫn không được chứa dấu nháy đơn: {value}")
    return f"subtitles=filename='{ass_path}':fontsdir='{fonts_dir}'"
