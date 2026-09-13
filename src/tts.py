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
import json
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


def timings_path(voice_path: Path) -> Path:
    """File sidecar chứa timing từng từ của một bản đọc.

    @param voice_path file mp3
    @returns đường dẫn `<mp3>.words.json`
    """
    return voice_path.with_suffix(f"{voice_path.suffix}.words.json")


def load_timings(voice_path: Path) -> list[dict]:
    """Đọc timing từ sidecar, trả [] nếu chưa có hoặc hỏng.

    @param voice_path file mp3
    @returns danh sách WordBoundary
    """
    path = timings_path(voice_path)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return list(data.get("words") or []) if isinstance(data, dict) else []


def save_timings(voice_path: Path, words: list[dict]) -> Path:
    """Ghi timing từng từ cạnh file mp3.

    @param voice_path file mp3
    @param words danh sách WordBoundary
    @returns đường dẫn sidecar
    """
    path = timings_path(voice_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"words": words}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return path


async def synth_with_timings(
    text: str, out_path: Path, voice: str, rate: str
) -> tuple[Path, list[dict]]:
    """Đọc thành mp3 và thu luôn timing từng từ (WordBoundary).

    @param text nội dung đọc
    @param out_path file mp3 đầu ra
    @param voice tên giọng Neural
    @param rate tốc độ, ví dụ '-8%'
    @returns (đường dẫn mp3, danh sách WordBoundary)
    :raises SystemExit nếu chưa cài edge-tts
    """
    try:
        import edge_tts
    except ImportError as exc:
        raise SystemExit("Chưa cài edge-tts. Chạy: pip install edge-tts") from exc

    out_path.parent.mkdir(parents=True, exist_ok=True)
    communicate = edge_tts.Communicate(text, voice=voice, rate=rate, boundary="WordBoundary")
    words: list[dict] = []
    with out_path.open("wb") as handle:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                handle.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                words.append({
                    "text": chunk["text"],
                    "offset": int(chunk["offset"]),
                    "duration": int(chunk["duration"]),
                })
    save_timings(out_path, words)
    return out_path, words


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
