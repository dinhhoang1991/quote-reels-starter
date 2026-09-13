# Ảnh chạy pipeline Reels: Python + FFmpeg, không cần cron của host (src/scheduler.py).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Ho_Chi_Minh

# ffmpeg: render video; tini: PID 1 để container nhận SIGTERM sạch sẽ
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Cài dependency trước để tận dụng cache layer khi chỉ sửa code
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Kiểm tra nhanh image có đủ ffmpeg + font + import được code
RUN python src/doctor.py && python -m compileall -q src

ENTRYPOINT ["/usr/bin/tini", "--", "/app/scripts/docker-entrypoint.sh"]
