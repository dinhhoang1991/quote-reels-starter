#!/usr/bin/env python3
"""Build a 9:16 Reels video: footage + overlay + voice + music."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WIDTH, HEIGHT, FPS = 1080, 1920, 30

sys.path.insert(0, str(ROOT / "src"))
from render_overlay import render_overlay  # noqa: E402


def run(cmd: list[str]) -> None:
    print("+", " ".join(cmd))
    subprocess.run(cmd, check=True)


def probe_duration(path: Path) -> float:
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
    return float(out)


def make_placeholder_footage(path: Path, seconds: float) -> Path:
    """Soft moving gradient so the pipeline runs without stock footage."""
    path.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"gradients=s={WIDTH}x{HEIGHT}:d={seconds:.2f}:speed=0.04:c0=0x061428:c1=0x1b5f73:c2=0x0b2a3a:n=3",
            "-r",
            str(FPS),
            "-pix_fmt",
            "yuv420p",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "20",
            str(path),
        ]
    )
    return path


def make_placeholder_music(path: Path, seconds: float) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=196:duration={seconds:.2f}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=247:duration={seconds:.2f}",
            "-filter_complex",
            "amix=inputs=2:duration=longest,volume=0.08,lowpass=f=1200",
            "-c:a",
            "aac",
            "-b:a",
            "96k",
            str(path),
        ]
    )
    return path


def first_file(folder: Path, exts: tuple[str, ...]) -> Path | None:
    if not folder.exists():
        return None
    files = [p for p in sorted(folder.iterdir()) if p.suffix.lower() in exts and p.is_file()]
    return files[0] if files else None


def build(json_path: Path, footage: Path | None, music: Path | None, voice: Path | None) -> Path:
    data = json.loads(json_path.read_text(encoding="utf-8"))
    clip_id = data.get("id", json_path.stem)

    overlay = ROOT / "assets" / "overlays" / f"{clip_id}.png"
    render_overlay(data, ROOT / "assets" / "fonts", overlay)

    if voice is None:
        voice = ROOT / "assets" / "voice" / f"{clip_id}.mp3"
        if not voice.exists():
            from tts import synth
            import asyncio

            script = data.get("voice_script") or data["title"]
            asyncio.run(synth(script, voice, "vi-VN-NamMinhNeural", "-8%"))

    duration = probe_duration(voice) + 1.8

    if footage is None:
        footage = first_file(ROOT / "assets" / "footage", (".mp4", ".mov", ".mkv", ".webm"))
    if footage is None:
        footage = make_placeholder_footage(ROOT / "assets" / "footage" / "_placeholder.mp4", max(duration, 12))

    if music is None:
        music = first_file(ROOT / "assets" / "music", (".mp3", ".wav", ".m4a", ".aac"))
    if music is None:
        music = make_placeholder_music(ROOT / "assets" / "music" / "_placeholder.m4a", duration)

    out = ROOT / "assets" / "out" / f"{clip_id}.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)

    filter_complex = (
        f"[0:v]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={WIDTH}:{HEIGHT},fps={FPS},setsar=1[bg];"
        f"[bg][3:v]overlay=0:0:format=auto[v];"
        f"[2:a]volume=0.12[m];"
        f"[1:a][m]amix=inputs=2:duration=first:dropout_transition=2,"
        f"loudnorm=I=-16:TP=-1.5:LRA=11[a]"
    )

    run(
        [
            "ffmpeg",
            "-y",
            "-stream_loop",
            "-1",
            "-i",
            str(footage),
            "-i",
            str(voice),
            "-stream_loop",
            "-1",
            "-i",
            str(music),
            "-i",
            str(overlay),
            "-filter_complex",
            filter_complex,
            "-map",
            "[v]",
            "-map",
            "[a]",
            "-t",
            f"{duration:.2f}",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            str(out),
        ]
    )
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Render 1 Reels từ file JSON")
    parser.add_argument("--json", required=True)
    parser.add_argument("--footage", default="")
    parser.add_argument("--music", default="")
    parser.add_argument("--voice", default="")
    args = parser.parse_args()

    out = build(
        Path(args.json),
        Path(args.footage) if args.footage else None,
        Path(args.music) if args.music else None,
        Path(args.voice) if args.voice else None,
    )
    print(f"\nXONG: {out}")


if __name__ == "__main__":
    main()
