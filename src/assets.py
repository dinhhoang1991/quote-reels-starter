#!/usr/bin/env python3
"""Chọn nền/nhạc cho từng clip: luân phiên hoặc ngẫu nhiên, tránh lặp lại gần đây.

Thư mục `assets/footage` và `assets/music` có bao nhiêu file cũng được — trước đây
pipeline luôn lấy file đầu alphabet nên mọi clip dùng đúng một nền và một bài nhạc.
Ở đây `pick_files` là hàm thuần (không I/O) để test được, còn state lưu ở
`data/asset_rotation.json` để lần chạy sau biết đã dùng tới đâu.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

from config import load_config, resolve_path

STILL_EXT = (".jpg", ".jpeg", ".png", ".webp")
VIDEO_EXT = (".mp4", ".mov", ".mkv", ".webm")
FOOTAGE_EXT = VIDEO_EXT + STILL_EXT
MUSIC_EXT = (".mp3", ".wav", ".m4a", ".aac")
PICK_MODES = ("first", "rotate", "random")


def list_media(folder: Path, exts: tuple[str, ...]) -> list[Path]:
    """File media dùng được trong thư mục, sắp theo alphabet, bỏ file `_placeholder`.

    @param folder thư mục cần quét
    @param exts đuôi file chấp nhận
    @returns danh sách đường dẫn (rỗng nếu thư mục không có)
    """
    if not folder.exists():
        return []
    return [
        p
        for p in sorted(folder.iterdir())
        if p.suffix.lower() in exts and p.is_file() and not p.name.startswith("_placeholder")
    ]


def settings(cfg=None) -> dict:
    """Tuỳ chọn chọn asset từ config (`assets.*`), đã áp mặc định.

    @param cfg config, mặc định đọc config.yaml
    @returns {"pick": str, "recent": int, "segment_seconds": float}
    """
    cfg = cfg or load_config()
    section = cfg.get("assets", {}) or {}
    pick = str(section.get("pick", "rotate") or "rotate").strip().lower()
    if pick not in PICK_MODES:
        raise SystemExit(f"assets.pick không hợp lệ: {pick!r} (chọn {' | '.join(PICK_MODES)})")
    return {
        "pick": pick,
        "recent": max(int(section.get("recent", 3) or 0), 0),
        "segment_seconds": max(float(section.get("segment_seconds", 0) or 0), 0.0),
    }


def state_file(cfg=None) -> Path:
    """Đường dẫn file state luân phiên asset.

    @param cfg config, mặc định đọc config.yaml
    @returns đường dẫn trong repo (mặc định `data/asset_rotation.json`)
    """
    cfg = cfg or load_config()
    section = cfg.get("assets", {}) or {}
    return resolve_path(str(section.get("state_file", "data/asset_rotation.json")))


def load_state(path: Path) -> dict:
    """Đọc state; file thiếu/hỏng thì coi như rỗng (không làm hỏng lượt render).

    @param path file state
    @returns dict state
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_state(path: Path, state: dict) -> None:
    """Ghi state kiểu atomic để cron chạy chồng không đọc phải file dở.

    @param path file state
    @param state dict state
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def pick_files(
    kind: str,
    files: list[Path],
    count: int,
    mode: str = "rotate",
    recent: int = 0,
    state: dict | None = None,
    rng: random.Random | None = None,
) -> tuple[list[Path], dict]:
    """Chọn `count` file theo `mode`, trả kèm state mới. Hàm thuần, không I/O.

    - `first`: `count` file đầu (hành vi cũ).
    - `rotate`: lần lượt theo alphabet, mỗi lần gọi tiến thêm `count` bước — mọi clip
      khác nhau cho tới khi quay vòng hết thư mục.
    - `random`: ngẫu nhiên, tránh `recent` file vừa dùng gần nhất (nếu còn lựa chọn khác).

    @param kind khoá state ("footage" hoặc "music")
    @param files danh sách file có thể chọn (đã sắp theo alphabet)
    @param count số file cần lấy
    @param mode một trong PICK_MODES
    @param recent số file gần nhất cần tránh (chỉ có tác dụng với `random`)
    @param state state cũ, None coi như rỗng
    @param rng nguồn ngẫu nhiên (truyền seed để test)
    @returns (danh sách file đã chọn, state mới)
    """
    state = dict(state or {})
    total = len(files)
    if total == 0 or count <= 0:
        return [], state
    if mode not in PICK_MODES:
        raise SystemExit(f"assets.pick không hợp lệ: {mode!r} (chọn {' | '.join(PICK_MODES)})")

    if mode == "random":
        rng = rng or random.Random()
        avoid = {str(name) for name in (state.get(kind, {}).get("recent") or [])} if recent else set()
        pool = [p for p in files if p.name not in avoid] or list(files)
        picked: list[Path] = []
        while len(picked) < count:
            remaining = [p for p in pool if p not in picked] or [p for p in files if p not in picked]
            if not remaining:
                break
            take = min(count - len(picked), len(remaining))
            picked.extend(rng.sample(remaining, take))
        if recent:
            previous = [str(name) for name in (state.get(kind, {}).get("recent") or [])]
            names = previous + [p.name for p in picked]
            state[kind] = {"recent": names[-recent:]}
        return picked, state

    start = int((state.get(kind, {}) or {}).get("offset", 0)) % total
    picked = [files[(start + index) % total] for index in range(count)]
    if mode == "rotate":
        state[kind] = {"offset": (start + count) % total}
    return picked, state


def pick_for_clip(kind: str, files: list[Path], count: int, cfg=None) -> list[Path]:
    """Chọn file theo config và cập nhật state trên đĩa.

    @param kind khoá state ("footage" hoặc "music")
    @param files danh sách file có thể chọn
    @param count số file cần lấy
    @param cfg config, mặc định đọc config.yaml
    @returns danh sách file đã chọn
    """
    cfg = cfg or load_config()
    options = settings(cfg)
    path = state_file(cfg)
    state = load_state(path)
    picked, updated = pick_files(
        kind, files, count, options["pick"], options["recent"], state
    )
    if updated != state:
        save_state(path, updated)
    return picked
