#!/usr/bin/env python3
"""Dọn file cũ trong assets/: voice cache, overlay PNG, video đã render.

Mặc định chỉ LIỆT KÊ (`--dry-run`), phải thêm `--apply` mới xoá thật.

An toàn:
- không xoá file của clip đang nằm trong `data/queue/pending/` hoặc `failed/`
- không xoá file mới hơn `cleanup.keep_days`
- chỉ đụng tới các đuôi file pipeline tự sinh (.mp3/.png/.mp4) trong voice/overlay/out
"""

from __future__ import annotations

import argparse
import time
from dataclasses import dataclass
from pathlib import Path

from config import Cfg, load_config, resolve_path
from logutil import load_log, log
from schema import load_clip

DEFAULT_KEEP_DAYS = 30
TARGETS = (
    ("voice_dir", (".mp3", ".wav")),
    ("overlay_dir", (".png",)),
    ("out_dir", (".mp4",)),
)


@dataclass
class Candidate:
    """Một file đủ điều kiện xoá.

    @param path đường dẫn file
    @param age_days số ngày kể từ lần sửa cuối
    @param size_bytes dung lượng
    @param reason lý do được chọn (để in ra)
    """

    path: Path
    age_days: float
    size_bytes: int
    reason: str


def queued_clip_ids(cfg: Cfg | None = None) -> set[str]:
    """Id các clip đang chờ hoặc vừa lỗi — không được xoá file của chúng.

    @param cfg config, mặc định đọc config.yaml
    @returns tập id (tên file JSON bỏ đuôi)
    """
    cfg = cfg or load_config()
    base = resolve_path(cfg.paths.queue_dir)
    ids: set[str] = set()
    for status in ("pending", "failed"):
        for path in (base / status).glob("*.json"):
            try:
                data = load_clip(path)
            except Exception:  # JSON hỏng vẫn phải được bảo vệ theo tên file
                ids.add(path.stem)
            else:
                ids.add(str(data.get("id") or path.stem))
    return ids


def clip_id_of(path: Path) -> str:
    """Tách id clip từ tên file pipeline sinh ra.

    `clip_001.646d2e17d5.mp3` → `clip_001`; `clip_001_hook.png` → `clip_001`;
    `clip_001.mp4` → `clip_001`.

    @param path file trong assets/voice|overlays|out
    @returns id clip, hoặc tên file bỏ đuôi nếu không khớp dạng nào
    """
    stem = path.stem
    if stem.endswith("_hook"):
        stem = stem[: -len("_hook")]
    if "." in stem:
        head, _, tail = stem.rpartition(".")
        if tail and all(char in "0123456789abcdef" for char in tail.lower()):
            stem = head
    return stem


def published_clip_ids() -> set[str]:
    """Id các clip đã có trong data/published.json (kể cả DRAFT)."""
    return {
        str(post.get("clip_id"))
        for post in load_log().get("posts", [])
        if post.get("clip_id")
    }


def collect(
    cfg: Cfg | None = None,
    keep_days: float | None = None,
    now: float | None = None,
) -> list[Candidate]:
    """Liệt kê file cũ đủ điều kiện xoá.

    @param cfg config, mặc định đọc config.yaml
    @param keep_days số ngày giữ lại, mặc định `cleanup.keep_days`
    @param now mốc thời gian để tính tuổi (test truyền vào)
    @returns danh sách Candidate, cũ nhất trước
    """
    cfg = cfg or load_config()
    keep = float(keep_days if keep_days is not None else cfg.get("cleanup", {}).get(
        "keep_days", DEFAULT_KEEP_DAYS
    ))
    current = time.time() if now is None else now
    protected = queued_clip_ids(cfg)
    published = published_clip_ids()

    candidates: list[Candidate] = []
    for key, exts in TARGETS:
        folder = resolve_path(cfg.paths[key])
        if not folder.exists():
            continue
        for path in sorted(folder.iterdir()):
            if not path.is_file() or path.suffix.lower() not in exts:
                continue
            if path.name.startswith("_placeholder") or path.name == ".gitkeep":
                continue
            clip_id = clip_id_of(path)
            if clip_id in protected:
                continue
            try:
                mtime = path.stat().st_mtime
                size = path.stat().st_size
            except OSError:
                continue
            age_days = (current - mtime) / 86400
            if age_days < keep:
                continue
            reason = "đã đăng" if clip_id in published else "chưa đăng nhưng quá cũ"
            candidates.append(Candidate(path, age_days, size, reason))
    candidates.sort(key=lambda item: item.age_days, reverse=True)
    return candidates


def apply_cleanup(candidates: list[Candidate]) -> tuple[int, int]:
    """Xoá các file trong danh sách.

    @param candidates kết quả của collect()
    @returns (số file đã xoá, tổng byte đã giải phóng)
    """
    removed = 0
    freed = 0
    for item in candidates:
        try:
            item.path.unlink()
        except OSError as exc:
            log(f"không xoá được {item.path}: {exc}")
            continue
        removed += 1
        freed += item.size_bytes
    return removed, freed


def report(candidates: list[Candidate], keep_days: float, applied: bool) -> str:
    """Dòng tóm tắt để in ra và cho doctor đọc."""
    total = sum(item.size_bytes for item in candidates)
    action = "đã xoá" if applied else "sẽ xoá"
    return (
        f"{action} {len(candidates)} file cũ hơn {keep_days:.0f} ngày "
        f"({total / 1024 / 1024:.1f} MB)"
    )


def main() -> None:
    cfg = load_config()
    parser = argparse.ArgumentParser(description="Dọn file cũ trong assets/")
    parser.add_argument("--days", type=float, default=None, help="giữ file mới hơn N ngày")
    parser.add_argument("--apply", action="store_true", help="xoá thật (mặc định chỉ liệt kê)")
    args = parser.parse_args()

    keep = float(args.days if args.days is not None else cfg.get("cleanup", {}).get(
        "keep_days", DEFAULT_KEEP_DAYS
    ))
    candidates = collect(cfg, keep_days=keep)
    for item in candidates:
        log(f"{item.age_days:6.1f} ngày  {item.size_bytes / 1024:8.0f} KB  "
            f"{item.reason:<24} {item.path}")
    removed, freed = (0, 0)
    if args.apply:
        removed, freed = apply_cleanup(candidates)
    log(report(candidates, keep, args.apply))
    if args.apply:
        log(f"xoá {removed} file, giải phóng {freed / 1024 / 1024:.1f} MB")
    else:
        log("chạy lại với --apply để xoá thật")
    raise SystemExit(0)


if __name__ == "__main__":
    main()
