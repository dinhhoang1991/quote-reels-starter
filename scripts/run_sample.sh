#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
python3 src/make_video.py --json data/samples/clip_001.json
echo
echo "Mở file: $ROOT/assets/out/clip_001.mp4"
