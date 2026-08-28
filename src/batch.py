#!/usr/bin/env python3
"""Render every JSON in data/samples (or a folder you pass)."""

from __future__ import annotations

import argparse
from pathlib import Path

from make_video import build


def main() -> None:
    from config import root

    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", default=str(root() / "data" / "samples"))
    args = parser.parse_args()
    folder = Path(args.dir)
    files = sorted(folder.glob("*.json"))
    if not files:
        raise SystemExit(f"Không thấy file JSON trong {folder}")
    for json_path in files:
        print(f"\n===== {json_path.name} =====")
        out = build(json_path, None, None, None)
        print("->", out)


if __name__ == "__main__":
    main()
