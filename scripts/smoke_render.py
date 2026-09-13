#!/usr/bin/env python3
"""Render thật 1 clip ngắn bằng FFmpeg rồi kiểm spec đầu ra — dùng cho CI.

    python3 scripts/smoke_render.py [--seconds 4] [--keep]

Không cần mạng, không cần edge-tts: voice/nhạc/footage đều sinh bằng ffmpeg trong thư mục tạm.
Kiểm những thứ mà test đơn vị không bắt được vì chúng chỉ kiểm chuỗi filter:

- video 1080x1920 @ 30fps, H.264
- audio AAC 48 kHz stereo
- **độ dài audio == độ dài video** (đúng lỗi P0: `amix=duration=first` cắt audio theo voice)
- độ dài container khớp voice + head + tail và nằm trong khoảng min/max của config

Exit code 1 nếu có kiểm tra không đạt.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import config as config_module  # noqa: E402
from checks import audio_stream_seconds, ffmpeg_stream_info, probe_duration  # noqa: E402
from config import Cfg, load_config  # noqa: E402
from make_video import build  # noqa: E402

TOLERANCE = 0.15
CLIP = {
    "id": "smoke_001",
    "topic": "smoke",
    "title": "SMOKE TEST\nKIỂM AUDIO",
    "items": [
        {"label": "Mục một", "text": "Kiểm tra độ dài"},
        {"label": "Mục hai", "text": "Kiểm tra overlay"},
    ],
    "footer": "BẮT ĐẦU?",
    "caption": "Smoke test.",
    "voice_script": "Kiểm tra pipeline.",
}


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True, capture_output=True)


def make_inputs(work: Path, seconds: float) -> tuple[Path, Path, Path]:
    """Sinh voice (wav pcm, không cần encoder ngoài), nhạc (aac) và footage (ảnh tĩnh)."""
    voice = work / "voice.wav"
    music = work / "music.m4a"
    footage = work / "footage.jpg"
    run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", f"sine=frequency=210:duration={seconds}", "-ac", "1", "-ar", "24000", str(voice)])
    run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", f"anoisesrc=color=brown:d={seconds + 4}:a=0.2",
         "-af", "lowpass=f=500", "-c:a", "aac", "-ar", "48000", "-ac", "2", str(music)])
    # Ảnh tĩnh để nhánh Ken Burns (zoompan) cũng được chạy thật
    run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", "gradients=s=1080x1920:d=1:speed=0.05:c0=0x0b2a3a:c1=0x1b5f73:n=2",
         "-frames:v", "1", "-update", "1", str(footage)])
    return voice, music, footage


def write_fake_timings(voice: Path, seconds: float) -> Path:
    """Ghi sidecar WordBoundary giả để nhánh phụ đề burn-in cũng được chạy trong CI.

    Bản đọc của smoke test do ffmpeg sinh nên không có timing thật từ edge-tts.

    @param voice file voice đã sinh
    @param seconds độ dài bản đọc
    @returns đường dẫn sidecar
    """
    from subtitles import ms_to_offset
    from tts import timings_path

    words = ["Kiểm", "tra", "phụ", "đề", "cháy", "trong", "CI", "nhé"]
    step = seconds / len(words)
    entries = [
        {
            "text": word,
            "offset": ms_to_offset(int(index * step * 1000)),
            "duration": ms_to_offset(int(step * 0.85 * 1000)),
        }
        for index, word in enumerate(words)
    ]
    path = timings_path(voice)
    path.write_text(json.dumps({"words": entries}, ensure_ascii=False), encoding="utf-8")
    return path


def subtitle_pixels(video: Path, at_seconds: float, lower_from: int) -> int:
    """Đếm pixel gần trắng ở nửa dưới khung hình (chữ phụ đề).

    @param video file mp4
    @param at_seconds mốc lấy frame
    @param lower_from dòng bắt đầu quét
    @returns số pixel trắng tìm được
    """
    from PIL import Image

    frame = video.parent / "subtitle-frame.png"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-ss", f"{at_seconds:.2f}", "-i", str(video),
         "-frames:v", "1", str(frame)],
        check=True, capture_output=True,
    )
    image = Image.open(frame).convert("RGB")
    pixels = image.load()
    count = 0
    for y in range(lower_from, image.height, 2):
        for x in range(0, image.width, 2):
            red, green, blue = pixels[x, y]
            if red > 230 and green > 230 and blue > 230:
                count += 1
    return count


def check(name: str, ok: bool, detail: str) -> bool:
    print(f"  [{'ok' if ok else 'FAIL'}] {name}: {detail}")
    return ok


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke render bằng ffmpeg")
    parser.add_argument("--seconds", type=float, default=4.0, help="độ dài voice sinh ra")
    parser.add_argument("--keep", action="store_true", help="giữ thư mục tạm để xem file")
    args = parser.parse_args()

    work = Path(tempfile.mkdtemp(prefix="smoke-render-"))
    print(f"thư mục tạm: {work}")

    # Chuyển đường dẫn ra file tạm để không đụng assets/ thật của repo
    raw = load_config().as_dict()
    for key, folder in (("voice_dir", "voice"), ("overlay_dir", "overlays"), ("out_dir", "out")):
        raw["paths"][key] = str(work / folder)
    config_module._cache = Cfg(raw)  # noqa: SLF001 — script test cố ý đổi cache config
    cfg = load_config()

    clip_path = work / "clip.json"
    clip_path.write_text(json.dumps(CLIP, ensure_ascii=False), encoding="utf-8")
    voice, music, footage = make_inputs(work, args.seconds)
    sidecar = write_fake_timings(voice, args.seconds)
    print(f"timing giả cho phụ đề: {sidecar.name}")

    print("render:")
    out = build(clip_path, footage, music, voice)

    head, tail = float(cfg.audio.head_seconds), float(cfg.audio.tail_seconds)
    voice_seconds = probe_duration(voice)
    expected = voice_seconds + head + tail
    info = ffmpeg_stream_info(out)
    video_seconds = probe_duration(out)
    audio_seconds = audio_stream_seconds(out, int(cfg.audio.sample_rate), int(cfg.audio.channels))

    print("kiểm tra:")
    results = [
        check("file ra tồn tại", out.exists() and out.stat().st_size > 10_000,
              f"{out.name} {out.stat().st_size / 1024:.0f} KB" if out.exists() else "không có"),
        check("video 9:16", info.get("video", {}).get("width") == int(cfg.video.width)
              and info.get("video", {}).get("height") == int(cfg.video.height),
              f"{info.get('video', {}).get('width')}x{info.get('video', {}).get('height')}"),
        check("fps", abs(float(info.get("video", {}).get("fps", 0)) - float(cfg.video.fps)) < 0.01,
              str(info.get("video", {}).get("fps"))),
        check("audio 48 kHz stereo",
              info.get("audio", {}).get("sample_rate") == int(cfg.audio.sample_rate)
              and info.get("audio", {}).get("layout") == "stereo",
              f"{info.get('audio', {}).get('sample_rate')} Hz {info.get('audio', {}).get('layout')}"),
        check("độ dài khớp voice + head + tail", abs(video_seconds - expected) < 0.35,
              f"video {video_seconds:.2f}s vs mong đợi {expected:.2f}s"),
        check("audio dài bằng video", abs(audio_seconds - video_seconds) < TOLERANCE,
              f"audio {audio_seconds:.2f}s / video {video_seconds:.2f}s "
              f"(lệch {abs(audio_seconds - video_seconds):.2f}s)"),
        check("trong khoảng Reels",
              float(cfg.video.min_seconds) <= video_seconds <= float(cfg.video.max_seconds),
              f"{video_seconds:.2f}s trong {cfg.video.min_seconds}–{cfg.video.max_seconds}s"),
    ]
    if bool((cfg.get("subtitles", {}) or {}).get("enabled", False)):
        at = float(cfg.audio.head_seconds) + max(args.seconds * 0.5, 0.5)
        white = subtitle_pixels(out, at, int(cfg.video.height) // 2)
        results.append(check("phụ đề burn-in", white > 200, f"{white} pixel trắng ở nửa dưới"))
        failed_names = ("file", "video", "fps", "audio", "duration", "a/v", "range", "subtitles")
    else:
        failed_names = ("file", "video", "fps", "audio", "duration", "a/v", "range")

    failed = [name for name, ok in zip(failed_names, results, strict=True) if not ok]
    if not args.keep:
        import shutil

        shutil.rmtree(work, ignore_errors=True)
    if failed:
        print(f"\nSMOKE RENDER THẤT BẠI: {', '.join(failed)}")
        raise SystemExit(1)
    print("\nSMOKE RENDER OK")
    raise SystemExit(0)


if __name__ == "__main__":
    main()
