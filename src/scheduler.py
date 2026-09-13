#!/usr/bin/env python3
"""Chạy lệnh theo lịch trong container (không cần cron của host).

    python3 src/scheduler.py                 # chạy `schedule.command` mỗi ngày 1 lần
    python3 src/scheduler.py --now           # chạy ngay 1 lần rồi vào lịch
    python3 src/scheduler.py --command "python3 src/cleanup.py --apply"

Giờ chạy lấy từ biến môi trường SCHEDULE_HOUR/SCHEDULE_MINUTE (ưu tiên) hoặc config.yaml.
"""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from config import Cfg, load_config
from logutil import log

DEFAULT_HOUR = 7
DEFAULT_MINUTE = 0
DEFAULT_COMMAND = "python3 src/publish.py --queue"


def _env_int(name: str, fallback: int) -> int:
    """Đọc biến môi trường dạng số; báo lỗi rõ ràng thay vì traceback ValueError."""
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return fallback
    try:
        return int(str(raw).strip())
    except ValueError as exc:
        raise SystemExit(f"{name} phải là số nguyên, đang là {raw!r}") from exc


def schedule_settings(cfg: Cfg | None = None) -> tuple[int, int, str]:
    """Giờ, phút và lệnh cần chạy theo lịch.

    Biến môi trường SCHEDULE_HOUR / SCHEDULE_MINUTE / SCHEDULE_COMMAND ghi đè config.

    @param cfg config, mặc định đọc config.yaml
    @returns (giờ, phút, lệnh)
    """
    cfg = cfg or load_config()
    section = cfg.get("schedule", {}) or {}
    hour = _env_int("SCHEDULE_HOUR", int(section.get("hour", DEFAULT_HOUR)))
    minute = _env_int("SCHEDULE_MINUTE", int(section.get("minute", DEFAULT_MINUTE)))
    command = str(os.getenv("SCHEDULE_COMMAND") or section.get("command", DEFAULT_COMMAND))
    if not 0 <= hour <= 23:
        raise SystemExit(f"schedule.hour phải trong 0–23, đang là {hour}")
    if not 0 <= minute <= 59:
        raise SystemExit(f"schedule.minute phải trong 0–59, đang là {minute}")
    return hour, minute, command


def seconds_until(hour: int, minute: int, now: datetime | None = None) -> float:
    """Số giây từ `now` tới mốc giờ:phút kế tiếp.

    @param hour giờ (0–23)
    @param minute phút (0–59)
    @param now mốc thời gian, mặc định thời điểm hiện tại (UTC)
    @returns số giây, luôn > 0; nếu đang đúng mốc thì tính cho ngày mai
    """
    current = now or datetime.now(timezone.utc)
    target = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= current:
        target += timedelta(days=1)
    return (target - current).total_seconds()


def run_daily(
    command: str,
    hour: int,
    minute: int,
    sleep: Callable[[float], None] = time.sleep,
    now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    max_runs: int | None = None,
) -> int:
    """Chạy `command` mỗi ngày 1 lần vào giờ:phút, cách nhau bằng `sleep`.

    @param command lệnh shell (tách bằng shlex)
    @param max_runs dừng sau N lần chạy (test truyền vào), None = chạy mãi
    @returns số lần đã chạy
    """
    argv = shlex.split(command)
    if not argv:
        raise SystemExit("schedule.command rỗng")
    runs = 0
    while max_runs is None or runs < max_runs:
        wait = seconds_until(hour, minute, now_fn())
        log(f"lần chạy kế tiếp sau {wait / 3600:.2f}h ({hour:02d}:{minute:02d} UTC)")
        sleep(wait)
        log(f"chạy: {command}")
        result = run(argv, check=False)
        runs += 1
        if result.returncode != 0:
            log(f"lệnh trả về exit code {result.returncode} (vẫn tiếp tục lịch)")
    return runs


def main() -> None:
    cfg = load_config()
    hour, minute, command = schedule_settings(cfg)
    parser = argparse.ArgumentParser(description="Chạy lệnh theo lịch ngày")
    parser.add_argument("--now", action="store_true", help="chạy ngay 1 lần trước khi vào lịch")
    parser.add_argument("--command", default=command, help="lệnh cần chạy theo lịch")
    parser.add_argument("--once", action="store_true", help="chạy 1 lần rồi thoát")
    args = parser.parse_args()

    log(f"lịch: {args.command} mỗi ngày lúc {hour:02d}:{minute:02d} UTC")
    if args.now:
        log(f"chạy ngay: {args.command}")
        subprocess.run(shlex.split(args.command), check=False)
        if args.once:
            return
    run_daily(args.command, hour, minute, max_runs=1 if args.once else None)


if __name__ == "__main__":
    main()
