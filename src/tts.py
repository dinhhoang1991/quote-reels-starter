#!/usr/bin/env python3
"""Generate Vietnamese voiceover with edge-tts (free, no API key)."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


async def synth(text: str, out_path: Path, voice: str, rate: str) -> Path:
    try:
        import edge_tts
    except ImportError as exc:
        raise SystemExit("Chưa cài edge-tts. Chạy: pip install edge-tts") from exc

    out_path.parent.mkdir(parents=True, exist_ok=True)
    communicate = edge_tts.Communicate(text, voice=voice, rate=rate)
    await communicate.save(str(out_path))
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(description="TTS tiếng Việt")
    parser.add_argument("--json", required=True)
    parser.add_argument("--out", default="")
    parser.add_argument("--voice", default="vi-VN-NamMinhNeural")
    parser.add_argument("--rate", default="-8%")
    args = parser.parse_args()

    data = json.loads(Path(args.json).read_text(encoding="utf-8"))
    text = data.get("voice_script") or data.get("title", "")
    out = Path(args.out) if args.out else ROOT / "assets" / "voice" / f"{data.get('id', 'clip')}.mp3"
    path = asyncio.run(synth(text, out, args.voice, args.rate))
    print(path)


if __name__ == "__main__":
    main()
