#!/usr/bin/env python3
"""Render every JSON in data/samples (or a folder you pass)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from make_video import build  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", default=str(ROOT / "data" / "samples"))
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
