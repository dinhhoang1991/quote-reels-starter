#!/usr/bin/env python3
"""Build a 9:16 Reels video: footage + hook/full overlay + voice + ducked music.

Audio: AAC stereo 48 kHz. GOP closed 2s. Duration clamped 3–90s.
Hook overlay for the first N seconds so the title is readable muted.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from checks import clamp_duration, fonts_ok, probe_duration, require_ffmpeg
from config import load_config, resolve_path, root
from render_overlay import render_pair
from schema import load_clip

ROOT = root()


def run(cmd: list[str]) -> None:
    print("+", " ".join(cmd))
    subprocess.run(cmd, check=True)


def first_file(folder: Path, exts: tuple[str, ...]) -> Path | None:
    if not folder.exists():
        return None
    files = [
        p
        for p in sorted(folder.iterdir())
        if p.suffix.lower() in exts and p.is_file() and not p.name.startswith("_placeholder")
    ]
    return files[0] if files else None


def make_placeholder_footage(path: Path, seconds: float, width: int, height: int, fps: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"gradients=s={width}x{height}:d={seconds:.2f}:speed=0.04:c0=0x061428:c1=0x1b5f73:c2=0x0b2a3a:n=3",
            "-r",
            str(fps),
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
            f"anoisesrc=color=brown:d={seconds:.2f}:a=0.15",
            "-af",
            "lowpass=f=600,highpass=f=80,volume=0.35",
            "-c:a",
            "aac",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-b:a",
            "96k",
            str(path),
        ]
    )
    return path


def ensure_voice(data: dict, voice: Path | None) -> Path:
    cfg = load_config()
    if voice is not None:
        if not voice.exists():
            raise SystemExit(f"Không thấy file giọng: {voice}")
        return voice
    dest = resolve_path(cfg.paths.voice_dir) / f"{data['id']}.mp3"
    if dest.exists():
        return dest
    from tts import synth
    import asyncio

    asyncio.run(
        synth(data["voice_script"], dest, cfg.audio.voice_name, cfg.audio.voice_rate)
    )
    return dest


def build(
    json_path: Path,
    footage: Path | None,
    music: Path | None,
    voice: Path | None,
) -> Path:
    require_ffmpeg()
    fonts_ok()
    cfg = load_config()
    data = load_clip(json_path)

    width, height, fps = int(cfg.video.width), int(cfg.video.height), int(cfg.video.fps)
    fonts_dir = resolve_path(cfg.paths.fonts_dir)
    overlay_dir = resolve_path(cfg.paths.overlay_dir)
    hook_png, full_png = render_pair(data, fonts_dir, overlay_dir)

    voice = ensure_voice(data, voice)
    voice_dur = probe_duration(voice)
    head = float(cfg.audio.head_seconds)
    tail = float(cfg.audio.tail_seconds)
    duration = clamp_duration(voice_dur + head + tail)
    hook_s = min(float(cfg.hook.seconds), max(duration - 0.4, 0.8))

    still_ext = (".jpg", ".jpeg", ".png", ".webp")
    video_ext = (".mp4", ".mov", ".mkv", ".webm")
    if footage is None:
        footage = first_file(resolve_path(cfg.paths.footage_dir), video_ext + still_ext)
    if footage is None:
        footage = make_placeholder_footage(
            resolve_path(cfg.paths.footage_dir) / "_placeholder.mp4",
            max(duration, 12),
            width,
            height,
            fps,
        )
    is_still = footage.suffix.lower() in still_ext

    if music is None:
        music = first_file(resolve_path(cfg.paths.music_dir), (".mp3", ".wav", ".m4a", ".aac"))
    if music is None:
        music = make_placeholder_music(
            resolve_path(cfg.paths.music_dir) / "_placeholder.m4a", duration
        )

    out = resolve_path(cfg.paths.out_dir) / f"{data['id']}.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)

    sr = int(cfg.audio.sample_rate)
    ch = int(cfg.audio.channels)
    vol = float(cfg.audio.music_volume)
    duck = cfg.audio.ducking
    head_ms = int(head * 1000)
    fade_out_start = max(duration - 0.5, 0.2)
    gop = int(cfg.video.gop)
    crf = int(cfg.video.crf)
    ken = bool(cfg.video.get("ken_burns", True)) and is_still

    if ken:
        frames = int(duration * fps) + 2
        bg = (
            f"[0:v]scale={width * 12 // 10}:{height * 12 // 10},"
            f"zoompan=z='min(zoom+0.00055,1.12)':x='iw/2-(iw/zoom/2)':"
            f"y='ih/2-(ih/zoom/2)':d={frames}:s={width}x{height}:fps={fps},"
            f"setsar=1,fade=t=in:st=0:d=0.4[bg];"
        )
    else:
        bg = (
            f"[0:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},fps={fps},setsar=1,fade=t=in:st=0:d=0.4[bg];"
        )

    filter_complex = (
        bg
        + f"[bg][3:v]overlay=0:0:enable='lt(t,{hook_s:.2f})':format=auto[v1];"
        + f"[v1][4:v]overlay=0:0:enable='gte(t,{hook_s:.2f})':format=auto,"
        + f"fade=t=out:st={fade_out_start:.2f}:d=0.45[v];"
        + f"[1:a]aresample={sr},aformat=channel_layouts=stereo,adelay={head_ms}|{head_ms}[voice];"
        + f"[voice]asplit=2[voice_mix][voice_sc];"
        + f"[2:a]aresample={sr},aformat=channel_layouts=stereo,volume={vol}[m];"
        + f"[m][voice_sc]sidechaincompress=threshold={duck.threshold}:ratio={duck.ratio}:"
        + f"attack={duck.attack}:release={duck.release}:makeup=2[ducked];"
        + f"[voice_mix][ducked]amix=inputs=2:duration=first:dropout_transition=2,"
        + f"loudnorm=I=-16:TP=-1.5:LRA=11,"
        + f"aformat=sample_fmts=fltp:sample_rates={sr}:channel_layouts=stereo,"
        + f"afade=t=in:st=0:d=0.25,afade=t=out:st={fade_out_start:.2f}:d=0.45[a]"
    )

    cmd = [
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
        "-loop",
        "1",
        "-t",
        f"{duration:.2f}",
        "-i",
        str(hook_png),
        "-loop",
        "1",
        "-t",
        f"{duration:.2f}",
        "-i",
        str(full_png),
        "-filter_complex",
        filter_complex,
        "-map",
        "[v]",
        "-map",
        "[a]",
        "-t",
        f"{duration:.2f}",
        "-r",
        str(fps),
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        str(crf),
        "-pix_fmt",
        "yuv420p",
        "-profile:v",
        "high",
        "-level",
        "4.0",
        "-g",
        str(gop),
        "-keyint_min",
        str(gop),
        "-sc_threshold",
        "0",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-ar",
        str(sr),
        "-ac",
        str(ch),
        "-movflags",
        "+faststart",
        str(out),
    ]
    run(cmd)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Render 1 Reels từ file JSON")
    parser.add_argument("--json", required=True)
    parser.add_argument("--footage", default="")
    parser.add_argument("--music", default="")
    parser.add_argument("--voice", default="", help="Đường dẫn file mp3 (Vbee/FPT/edge-tts)")
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
