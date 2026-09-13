#!/usr/bin/env python3
"""Preflight: ffmpeg, fonts, duration clamp, Reels audio spec."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from config import load_config, resolve_path

VIDEO_SIZE_RE = re.compile(r"\b(\d{2,5})x(\d{2,5})\b")
FPS_RE = re.compile(r"([\d.]+)\s+fps")
SAMPLE_RATE_RE = re.compile(r"(\d{2,6})\s*Hz")
CHANNELS = {"mono": 1, "stereo": 2, "2.1": 3, "5.1": 6, "7.1": 8}
S16LE_BYTES_PER_SAMPLE = 2
# Encoder/filter pipeline bắt buộc phải có; thiếu là render chết giữa chừng.
REQUIRED_ENCODERS = ("libx264", "aac")
REQUIRED_FILTERS = ("zoompan", "sidechaincompress", "loudnorm", "gradients", "anoisesrc")
# Chỉ cần khi bật phụ đề burn-in (libass).
OPTIONAL_FILTERS = ("subtitles",)


def require_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None:
        raise SystemExit("Thiếu ffmpeg. Cài FFmpeg (https://ffmpeg.org) rồi chạy lại.")
    if shutil.which("ffprobe") is None:
        print("Cảnh báo: không thấy ffprobe — dùng ffmpeg để đọc duration.")


def probe_duration(path: Path) -> float:
    if shutil.which("ffprobe"):
        out = subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            text=True,
        ).strip()
        try:
            return float(out)
        except ValueError as exc:
            raise SystemExit(f"Không đọc được duration của {path}: {out!r}") from exc

    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(path)],
        capture_output=True,
        text=True,
    )
    blob = (proc.stderr or "") + (proc.stdout or "")
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", blob)
    if not match:
        raise SystemExit(f"Không đọc được duration của {path}")
    hours, minutes, seconds = int(match.group(1)), int(match.group(2)), float(match.group(3))
    return hours * 3600 + minutes * 60 + seconds


def parse_stream_info(blob: str) -> dict:
    """Bóc thông số stream từ output `ffmpeg -i` (không cần ffprobe).

    @param blob stderr+stdout của ffmpeg
    @returns {"video": {"width","height","fps"}, "audio": {"sample_rate","channels","layout"},
              "duration"} — khoá nào không đọc được thì bỏ
    """
    info: dict = {}
    duration = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", blob)
    if duration:
        info["duration"] = (
            int(duration.group(1)) * 3600 + int(duration.group(2)) * 60 + float(duration.group(3))
        )
    for line in blob.splitlines():
        stripped = line.strip()
        if not stripped.startswith("Stream #"):
            continue
        if "Video:" in stripped:
            size = VIDEO_SIZE_RE.search(stripped)
            fps = FPS_RE.search(stripped)
            video = {}
            if size:
                video["width"], video["height"] = int(size.group(1)), int(size.group(2))
            if fps:
                video["fps"] = float(fps.group(1))
            if video:
                info["video"] = video
        elif "Audio:" in stripped:
            rate = SAMPLE_RATE_RE.search(stripped)
            audio = {}
            if rate:
                audio["sample_rate"] = int(rate.group(1))
            for layout, channels in CHANNELS.items():
                if re.search(rf"\b{re.escape(layout)}\b", stripped):
                    audio["layout"] = layout
                    audio["channels"] = channels
                    break
            if audio:
                info["audio"] = audio
    return info


def ffmpeg_stream_info(path: Path) -> dict:
    """Chạy ffmpeg để đọc thông số stream của file.

    @param path file media
    @returns kết quả của parse_stream_info
    """
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(path)],
        capture_output=True,
        text=True,
    )
    return parse_stream_info(f"{proc.stderr or ''}{proc.stdout or ''}")


def audio_stream_seconds(path: Path, sample_rate: int | None = None, channels: int = 2) -> float:
    """Đo độ dài thật của stream audio bằng cách decode sang PCM và đếm byte.

    Không cần ffprobe, và đo đúng stream audio (container duration là của stream dài nhất).

    @param path file media
    @param sample_rate sample rate dùng khi decode, mặc định theo config
    @param channels số kênh dùng khi decode
    @returns số giây; 0.0 nếu không decode được
    """
    cfg = load_config()
    rate = int(sample_rate or cfg.audio.sample_rate)
    proc = subprocess.run(
        [
            "ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a:0",
            "-f", "s16le", "-acodec", "pcm_s16le", "-ar", str(rate), "-ac", str(channels), "-",
        ],
        capture_output=True,
    )
    if proc.returncode != 0 or not proc.stdout:
        return 0.0
    samples = len(proc.stdout) / (S16LE_BYTES_PER_SAMPLE * max(channels, 1))
    return samples / rate


def parse_capabilities(blob: str) -> set[str]:
    """Bóc tên encoder/filter từ output `ffmpeg -encoders` / `-filters`.

    Encoder có cột cờ 6 ký tự (` V....D libx264 ...`), filter có cột cờ 3–4 ký tự
    (`.SC zoompan`), và phần chú giải cũng có dạng `T.. = Timeline support` nên phải loại.

    @param blob stderr+stdout của ffmpeg
    @returns tập tên (đã lowercase)
    """
    names: set[str] = set()
    for line in blob.splitlines():
        if " = " in line:
            continue
        match = re.match(r"^\s*[A-Z.]{3,6}\s+([A-Za-z0-9_]+)\s", line)
        if match:
            names.add(match.group(1).lower())
    return names


def ffmpeg_capabilities() -> tuple[set[str], set[str]]:
    """Encoder và filter mà ffmpeg hiện có.

    @returns (encoders, filters); rỗng nếu không chạy được ffmpeg
    """
    result: list[set[str]] = []
    for flag in ("-encoders", "-filters"):
        proc = subprocess.run(["ffmpeg", "-hide_banner", flag], capture_output=True, text=True)
        result.append(parse_capabilities(f"{proc.stderr or ''}{proc.stdout or ''}"))
    return result[0], result[1]


def ffmpeg_gaps(cfg=None) -> tuple[list[str], list[str]]:
    """Encoder/filter còn thiếu so với yêu cầu của pipeline.

    @param cfg config, mặc định đọc config.yaml (bật phụ đề thì `subtitles` thành bắt buộc)
    @returns (thiếu bắt buộc, thiếu tuỳ chọn)
    """
    cfg = cfg or load_config()
    encoders, filters = ffmpeg_capabilities()
    required_filters = list(REQUIRED_FILTERS)
    optional_filters = list(OPTIONAL_FILTERS)
    if bool((cfg.get("subtitles", {}) or {}).get("enabled", False)):
        # bật phụ đề thì libass trở thành bắt buộc
        required_filters += [name for name in optional_filters if name == "subtitles"]
        optional_filters = [name for name in optional_filters if name != "subtitles"]
    missing_encoders = [name for name in REQUIRED_ENCODERS if name not in encoders]
    missing_filters = [name for name in required_filters if name not in filters]
    optional = [name for name in optional_filters if name not in filters]
    return missing_encoders + missing_filters, optional


def clamp_duration(seconds: float) -> float:
    cfg = load_config()
    lo = float(cfg.video.min_seconds)
    hi = float(cfg.video.max_seconds)
    if seconds < lo:
        print(f"Duration {seconds:.1f}s < {lo}s — kéo lên tối thiểu Reels.")
        return lo
    if seconds > hi:
        print(f"Duration {seconds:.1f}s > {hi}s — cắt còn {hi}s (giới hạn Reels API).")
        return hi
    return seconds


def parse_rate_percent(rate: str) -> float:
    """Đổi rate của edge-tts thành số phần trăm: '-8%' -> -8.0, giá trị lạ -> 0.0."""
    try:
        return float(str(rate).strip().rstrip("%"))
    except ValueError:
        return 0.0


def estimate_voice_seconds(text: str, rate: str, chars_per_second: float) -> float:
    """Ước lượng thời lượng đọc từ số ký tự, trước khi gọi TTS.

    Heuristic để bắt sớm script quá dài; sai số ~±15% nên chỉ dùng cho cảnh báo,
    không dùng để cắt audio.

    @param text voice_script
    @param rate rate của edge-tts, ví dụ '-8%'
    @param chars_per_second số ký tự đọc được mỗi giây ở rate 0%
    @returns số giây ước lượng, 0.0 nếu tham số không dùng được
    """
    chars = len(" ".join(str(text).split()))
    speed = 1.0 + parse_rate_percent(rate) / 100.0
    if chars_per_second <= 0 or speed <= 0:
        return 0.0
    return chars / (chars_per_second * speed)



def fonts_ok() -> None:
    cfg = load_config()
    fonts_dir = resolve_path(cfg.paths.fonts_dir)
    for name in (cfg.paths.title_font, cfg.paths.body_font, cfg.paths.footer_font):
        path = fonts_dir / name
        if not path.exists():
            raise SystemExit(f"Thiếu font: {path}")
