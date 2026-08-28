#!/usr/bin/env bash
# Chuẩn hóa mọi video trong assets/footage về 1080x1920, 30fps, bỏ tiếng gốc.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
IN="$ROOT/assets/footage"
TMP="$IN/_normalized"
mkdir -p "$TMP"

shopt -s nullglob
for f in "$IN"/*.{mp4,mov,mkv,webm,MP4,MOV}; do
  base="$(basename "${f%.*}")"
  [[ "$base" == _placeholder ]] && continue
  echo "Normalize $f"
  ffmpeg -y -i "$f" \
    -vf "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,fps=30" \
    -an -c:v libx264 -preset fast -crf 20 -pix_fmt yuv420p \
    "$TMP/${base}.mp4"
done

if compgen -G "$TMP/*.mp4" > /dev/null; then
  echo "File chuẩn nằm ở $TMP — kiểm tra rồi copy đè vào $IN nếu ưng."
fi
