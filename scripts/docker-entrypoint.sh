#!/usr/bin/env bash
# Entrypoint container: kiểm tra môi trường rồi vào lịch chạy publish.py --queue.
# DOCTOR_STRICT=1 → dừng container nếu doctor có [fail] (mặc định chỉ cảnh báo).
set -euo pipefail
cd /app
mkdir -p logs

if ! python3 src/doctor.py; then
  if [ "${DOCTOR_STRICT:-0}" = "1" ]; then
    echo "doctor có [fail] và DOCTOR_STRICT=1 — dừng container" >&2
    exit 1
  fi
  echo "cảnh báo: doctor có [fail], vẫn chạy tiếp (đặt DOCTOR_STRICT=1 để dừng)" >&2
fi

exec python3 src/scheduler.py "$@"
