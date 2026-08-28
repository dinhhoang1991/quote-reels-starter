#!/usr/bin/env python3
"""Preflight: ffmpeg, fonts, duration clamp, Reels audio spec."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from config import load_config, resolve_path


def require_ffmpeg() -> None:
    for binary in ("ffmpeg", "ffprobe"):
        if shutil.which(binary) is None:
            raise SystemExit(f"Thiếu {binary}. Cài FFmpeg (https://ffmpeg.org) rồi chạy lại.")


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
    try:
        return float(out)
    except ValueError as exc:
        raise SystemExit(f"Không đọc được duration của {path}: {out!r}") from exc


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


def fonts_ok() -> None:
    cfg = load_config()
    fonts_dir = resolve_path(cfg.paths.fonts_dir)
    for name in (cfg.paths.title_font, cfg.paths.body_font, cfg.paths.footer_font):
        path = fonts_dir / name
        if not path.exists():
            raise SystemExit(f"Thiếu font: {path}")
