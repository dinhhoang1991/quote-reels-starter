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
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from config import Cfg, load_config
from logutil import log
from notify import notify

DEFAULT_HOUR = 7
DEFAULT_MINUTE = 0
DEFAULT_COMMAND = "python3 src/publish.py --queue"
DEFAULT_TIMEZONE = "UTC"


def _env_int(name: str, fallback: int) -> int:
    """Đọc biến môi trường dạng số; báo lỗi rõ ràng thay vì traceback ValueError."""
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return fallback
    try:
        return int(str(raw).strip())
    except ValueError as exc:
        raise SystemExit(f"{name} phải là số nguyên, đang là {raw!r}") from exc


def timezone_of(name: str) -> ZoneInfo:
    """Đổi tên vùng giờ IANA thành ZoneInfo, báo lỗi rõ nếu sai.

    @param name tên vùng giờ, ví dụ 'Asia/Ho_Chi_Minh'
    @returns ZoneInfo tương ứng
    :raises SystemExit nếu tên không hợp lệ
    """
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise SystemExit(
            f"schedule.timezone không hợp lệ: {name!r} — dùng tên IANA, ví dụ "
            f"'Asia/Ho_Chi_Minh' hoặc 'UTC' ({exc})"
        ) from exc


def schedule_settings(cfg: Cfg | None = None) -> tuple[int, int, str, str]:
    """Giờ, phút, lệnh và vùng giờ cần chạy theo lịch.

    Biến môi trường SCHEDULE_HOUR / SCHEDULE_MINUTE / SCHEDULE_COMMAND / SCHEDULE_TZ ghi
    đè config. Giờ được hiểu theo `schedule.timezone` (mặc định UTC).

    @param cfg config, mặc định đọc config.yaml
    @returns (giờ, phút, lệnh, tên vùng giờ)
    """
    cfg = cfg or load_config()
    section = cfg.get("schedule", {}) or {}
    hour = _env_int("SCHEDULE_HOUR", int(section.get("hour", DEFAULT_HOUR)))
    minute = _env_int("SCHEDULE_MINUTE", int(section.get("minute", DEFAULT_MINUTE)))
    command = str(os.getenv("SCHEDULE_COMMAND") or section.get("command", DEFAULT_COMMAND))
    tz_name = str(os.getenv("SCHEDULE_TZ") or section.get("timezone", DEFAULT_TIMEZONE))
    if not 0 <= hour <= 23:
        raise SystemExit(f"schedule.hour phải trong 0–23, đang là {hour}")
    if not 0 <= minute <= 59:
        raise SystemExit(f"schedule.minute phải trong 0–59, đang là {minute}")
    timezone_of(tz_name)  # kiểm tra sớm, sai thì báo ngay lúc đọc config
    return hour, minute, command, tz_name


def seconds_until(
    hour: int, minute: int, now: datetime | None = None, timezone_name: str = DEFAULT_TIMEZONE
) -> float:
    """Số giây từ `now` tới mốc giờ:phút kế tiếp theo vùng giờ.

    @param hour giờ (0–23) theo `timezone_name`
    @param minute phút (0–59)
    @param now mốc thời gian, mặc định thời điểm hiện tại
    @param timezone_name vùng giờ IANA, mặc định UTC
    @returns số giây, luôn > 0; nếu đang đúng mốc thì tính cho ngày mai
    """
    current = (now or datetime.now(timezone.utc)).astimezone(timezone_of(timezone_name))
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
    timezone_name: str = DEFAULT_TIMEZONE,
) -> int:
    """Chạy `command` mỗi ngày 1 lần vào giờ:phút (theo `timezone_name`).

    @param command lệnh shell (tách bằng shlex)
    @param timezone_name vùng giờ IANA để hiểu giờ:phút
    @param max_runs dừng sau N lần chạy (test truyền vào), None = chạy mãi
    @returns số lần đã chạy
    """
    argv = shlex.split(command)
    if not argv:
        raise SystemExit("schedule.command rỗng")
    runs = 0
    while max_runs is None or runs < max_runs:
        wait = seconds_until(hour, minute, now_fn(), timezone_name)
        log(f"lần chạy kế tiếp sau {wait / 3600:.2f}h "
            f"({hour:02d}:{minute:02d} {timezone_name})")
        sleep(wait)
        log(f"chạy: {command}")
        try:
            result = run(argv, check=False)
        except OSError as exc:
            log(f"không chạy được lệnh: {exc}")
            notify(f"Không chạy được lệnh theo lịch: {command} ({exc})", level="error")
            runs += 1
            continue
        runs += 1
        if result.returncode != 0:
            log(f"lệnh trả về exit code {result.returncode} (vẫn tiếp tục lịch)")
            notify(f"Lệnh theo lịch trả về exit code {result.returncode}: {command}", level="error")
    return runs


def main() -> None:
    cfg = load_config()
    hour, minute, command, tz_name = schedule_settings(cfg)
    parser = argparse.ArgumentParser(description="Chạy lệnh theo lịch ngày")
    parser.add_argument("--now", action="store_true", help="chạy ngay 1 lần trước khi vào lịch")
    parser.add_argument("--command", default=command, help="lệnh cần chạy theo lịch")
    parser.add_argument("--once", action="store_true", help="chạy 1 lần rồi thoát")
    args = parser.parse_args()

    log(f"lịch: {args.command} mỗi ngày lúc {hour:02d}:{minute:02d} {tz_name}")
    if args.now:
        log(f"chạy ngay: {args.command}")
        subprocess.run(shlex.split(args.command), check=False)
        if args.once:
            return
    run_daily(args.command, hour, minute, max_runs=1 if args.once else None, timezone_name=tz_name)


if __name__ == "__main__":
    main()
