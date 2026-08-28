#!/usr/bin/env python3
"""Pending / done / failed queue. Cron calls `publish.py --queue`."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from config import load_config, resolve_path
from schema import load_clip


def queue_dirs() -> dict[str, Path]:
    cfg = load_config()
    base = resolve_path(cfg.paths.queue_dir)
    dirs = {name: base / name for name in ("pending", "done", "failed")}
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)
    return dirs


def list_pending() -> list[Path]:
    pending = queue_dirs()["pending"]
    return sorted(p for p in pending.glob("*.json") if p.is_file())


def next_pending() -> Path | None:
    files = list_pending()
    return files[0] if files else None


def add(src: Path) -> Path:
    data = load_clip(src)
    dest = queue_dirs()["pending"] / f"{data['id']}.json"
    shutil.copy2(src, dest)
    return dest


def move(src: Path, status: str) -> Path:
    if status not in {"pending", "done", "failed"}:
        raise SystemExit(f"status lạ: {status}")
    dest = queue_dirs()[status] / src.name
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dest))
    return dest


def main() -> None:
    parser = argparse.ArgumentParser(description="Hàng chờ Reels")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    sub.add_parser("next")
    add_p = sub.add_parser("add")
    add_p.add_argument("json")
    move_p = sub.add_parser("move")
    move_p.add_argument("json")
    move_p.add_argument("--to", required=True, choices=("pending", "done", "failed"))
    args = parser.parse_args()

    if args.cmd == "list":
        files = list_pending()
        if not files:
            print("(pending trống)")
            return
        for p in files:
            print(p.name)
        return
    if args.cmd == "next":
        nxt = next_pending()
        if not nxt:
            raise SystemExit("Hàng chờ trống")
        print(nxt)
        return
    if args.cmd == "add":
        dest = add(Path(args.json))
        print(dest)
        return
    dest = move(Path(args.json), args.to)
    print(dest)


if __name__ == "__main__":
    main()
