#!/usr/bin/env python3
"""Vietnamese voiceover with edge-tts (free, no API key).

Giọng production (Vbee / FPT.AI): xuất mp3 rồi truyền --voice file vào make_video.py.

File mp3 được cache theo hash của (voice_script, giọng, rate): sửa lời thoại hoặc
đổi giọng là ra file mới, không dùng lại bản đọc cũ.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
from pathlib import Path

from config import Cfg, load_config, resolve_path, root
from schema import load_clip

ROOT = root()


def voice_cache_key(data: dict, cfg: Cfg) -> str:
    """Khoá cache cho bản đọc: đổi lời thoại, giọng hoặc rate là đổi khoá.

    @param data clip đã validate
    @param cfg config đang dùng
    @returns 10 ký tự hex
    """
    payload = "|".join(
        (
            str(data.get("voice_script", "")),
            str(cfg.audio.voice_name),
            str(cfg.audio.voice_rate),
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:10]


def voice_path(data: dict, cfg: Cfg | None = None) -> Path:
    """Đường dẫn mp3 cache của clip, tên kèm hash nội dung.

    @param data clip đã validate
    @param cfg config, mặc định đọc config.yaml
    @returns đường dẫn trong assets/voice/
    """
    cfg = cfg or load_config()
    return resolve_path(cfg.paths.voice_dir) / f"{data['id']}.{voice_cache_key(data, cfg)}.mp3"


async def synth(text: str, out_path: Path, voice: str, rate: str) -> Path:
    try:
        import edge_tts
    except ImportError as exc:
        raise SystemExit("Chưa cài edge-tts. Chạy: pip install edge-tts") from exc

    out_path.parent.mkdir(parents=True, exist_ok=True)
    communicate = edge_tts.Communicate(text, voice=voice, rate=rate)
    await communicate.save(str(out_path))
    return out_path


def synth_clip(data: dict, out_path: Path | None = None) -> Path:
    cfg = load_config()
    voice = cfg.audio.voice_name
    rate = cfg.audio.voice_rate
    if out_path is None:
        out_path = voice_path(data, cfg)
    return asyncio.run(synth(data["voice_script"], out_path, voice, rate))


def main() -> None:
    cfg = load_config()
    parser = argparse.ArgumentParser(description="TTS tiếng Việt")
    parser.add_argument("--json", required=True)
    parser.add_argument("--out", default="")
    parser.add_argument("--voice", default=cfg.audio.voice_name)
    parser.add_argument("--rate", default=cfg.audio.voice_rate)
    args = parser.parse_args()

    data = load_clip(Path(args.json))
    out = Path(args.out) if args.out else voice_path(data, cfg)
    path = asyncio.run(synth(data["voice_script"], out, args.voice, args.rate))
    print(path)


if __name__ == "__main__":
    main()
