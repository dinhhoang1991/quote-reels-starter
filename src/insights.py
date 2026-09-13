#!/usr/bin/env python3
"""Lấy Reels Insights cho các clip đã đăng, lưu vào data/insights.json.

    python3 src/insights.py refresh    # gọi Graph cho các Reel đã publish (cần token trong .env)
    python3 src/insights.py summary    # đọc file đã lưu, in top clip + tổng theo chủ đề

Metric của `video_insights` khác nhau theo loại video và quyền của token, nên danh sách
metric nằm trong `config.yaml` (`insights.metrics`). Nếu Graph từ chối cả lô, script thử lại
từng metric và ghi lại cái nào chạy được (`metrics_failed`).
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

from config import load_config, resolve_path, root
from logutil import PUBLISHED_STATE, load_log, log
from upload_facebook import DEFAULT_VERSION, load_env, request_with_retry

ROOT = root()
DEFAULT_METRICS = (
    "blue_reels_play_count",
    "post_video_views",
    "post_video_avg_time_watched",
    "post_reactions_by_type_total",
)
DEFAULT_LIMIT = 30
DEFAULT_RETRIES = 3


def insights_path() -> Path:
    cfg = load_config()
    return resolve_path(cfg.paths.get("insights_log", "data/insights.json"))


def load_insights() -> dict[str, Any]:
    """Đọc data/insights.json, trả về khung rỗng nếu chưa có hoặc hỏng."""
    path = insights_path()
    if not path.exists():
        return {"videos": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"videos": {}}
    return data if isinstance(data, dict) and "videos" in data else {"videos": {}}


def save_insights(data: dict[str, Any]) -> Path:
    """Ghi data/insights.json qua file tạm rồi os.replace."""
    path = insights_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def configured_metrics() -> list[str]:
    """Danh sách metric cần lấy, từ config hoặc mặc định."""
    cfg = load_config()
    metrics = (cfg.get("insights", {}) or {}).get("metrics") or list(DEFAULT_METRICS)
    return [str(metric) for metric in metrics]


def published_videos(limit: int = DEFAULT_LIMIT) -> list[dict[str, Any]]:
    """Các Reel đã publish, mới nhất trước, có video_id để gọi insights.

    @param limit số video tối đa
    @returns [{"clip_id", "video_id", "topic", "title", "iso"}]
    """
    posts = [
        post
        for post in load_log().get("posts", [])
        if str(post.get("state", "")).upper() == PUBLISHED_STATE and post.get("video_id")
    ]
    posts.sort(key=lambda post: float(post.get("ts", 0)), reverse=True)
    return [
        {
            "clip_id": post.get("clip_id", ""),
            "video_id": str(post["video_id"]),
            "topic": str(post.get("topic", "")),
            "title": str(post.get("title", "")),
            "iso": str(post.get("iso", "")),
        }
        for post in posts[:limit]
    ]


def parse_insights(payload: dict[str, Any]) -> dict[str, Any]:
    """Đổi response `video_insights` thành {metric: values}.

    @param payload body JSON của Graph
    @returns dict metric → danh sách values (giữ nguyên cấu trúc của Meta)
    """
    result: dict[str, Any] = {}
    for entry in payload.get("data") or []:
        if isinstance(entry, dict) and entry.get("name"):
            result[str(entry["name"])] = entry.get("values")
    return result


def _fetch_once(video_id: str, token: str, version: str, metrics: list[str], retries: int):
    return request_with_retry(
        "GET",
        f"https://graph.facebook.com/{version}/{video_id}/video_insights",
        retries,
        params={"metric": ",".join(metrics), "access_token": token},
        timeout=30,
    )


def fetch_video_insights(
    video_id: str, token: str, version: str, metrics: list[str], retries: int = DEFAULT_RETRIES
) -> tuple[dict[str, Any], list[str]]:
    """Lấy insights của 1 video, tự lùi về từng metric khi Graph từ chối cả lô.

    @param video_id id video Facebook
    @param token Page/user token có quyền đọc insights
    @param version phiên bản Graph API
    @param metrics danh sách metric muốn lấy
    @param retries số lần thử khi lỗi tạm (dùng chung backoff với upload)
    @returns (metrics đọc được, danh sách metric lỗi)
    """
    if not metrics:
        return {}, []
    resp = _fetch_once(video_id, token, version, metrics, retries)
    if resp.status_code < 400:
        return parse_insights(resp.json()), []
    if len(metrics) == 1:
        log(f"insights {video_id}: metric {metrics[0]} lỗi {resp.status_code}")
        return {}, list(metrics)

    log(f"insights {video_id}: lô metric lỗi {resp.status_code}, thử từng metric")
    found: dict[str, Any] = {}
    failed: list[str] = []
    for metric in metrics:
        single = _fetch_once(video_id, token, version, [metric], retries)
        if single.status_code < 400:
            found.update(parse_insights(single.json()))
        else:
            failed.append(metric)
    return found, failed


def refresh(limit: int = DEFAULT_LIMIT) -> dict[str, Any]:
    """Cập nhật data/insights.json cho các Reel đã publish.

    @param limit số video gần nhất
    @returns nội dung insights sau khi cập nhật
    :raises SystemExit khi thiếu token
    """
    load_env(ROOT / ".env")
    cfg = load_config()
    token = os.getenv("FB_PAGE_ACCESS_TOKEN", "").strip()
    if not token:
        raise SystemExit("Thiếu FB_PAGE_ACCESS_TOKEN trong .env — cần token để đọc insights.")
    version = os.getenv("FB_API_VERSION", str(cfg.facebook.api_version)).strip() or DEFAULT_VERSION
    metrics = configured_metrics()
    data = load_insights()
    videos = data.setdefault("videos", {})
    targets = published_videos(limit)
    if not targets:
        log("chưa có Reel nào đã publish trong data/published.json")
    for target in targets:
        values, failed = fetch_video_insights(
            target["video_id"], token, version, metrics,
            retries=int(cfg.facebook.max_retries),
        )
        entry = dict(target)
        entry["metrics"] = values
        entry["metrics_failed"] = failed
        entry["fetched_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        videos[target["video_id"]] = entry
        log(f"insights {target['clip_id']} ({target['video_id']}): "
            f"{len(values)} metric, {len(failed)} lỗi")
    data["fetched_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    path = save_insights(data)
    log(f"đã ghi {path}")
    return data


def metric_value(values: Any) -> float:
    """Rút 1 con số từ values của Meta (list/dict/scalar) để xếp hạng.

    @param values giá trị thô của 1 metric
    @returns số để so sánh, 0.0 nếu không đọc được
    """
    if isinstance(values, list) and values:
        first = values[0]
        if isinstance(first, dict):
            raw = first.get("value")
            if isinstance(raw, dict):
                return float(sum(v for v in raw.values() if isinstance(v, (int, float))))
            if isinstance(raw, (int, float)):
                return float(raw)
    if isinstance(values, (int, float)):
        return float(values)
    return 0.0


def topic_scores(data: dict[str, Any] | None = None) -> dict[str, float]:
    """Điểm hiệu quả trung bình theo chủ đề, đọc từ data/insights.json.

    Chỉ tính video có metric chính (metric đầu trong `insights.metrics`); video chưa đo
    được metric đó bị bỏ qua thay vì tính là 0.

    @param data nội dung insights, mặc định đọc file
    @returns {chủ đề: điểm trung bình}; chủ đề không có dữ liệu thì không xuất hiện
    """
    data = data if data is not None else load_insights()
    key = configured_metrics()[0]
    totals: dict[str, list[float]] = {}
    for item in (data.get("videos") or {}).values():
        topic = str(item.get("topic") or "").strip()
        metrics = item.get("metrics") or {}
        if not topic or key not in metrics:
            continue
        totals.setdefault(topic, []).append(metric_value(metrics[key]))
    return {
        topic: sum(values) / len(values) for topic, values in totals.items() if values
    }


def summary(data: dict[str, Any] | None = None, top: int = 5) -> list[str]:
    """Tóm tắt hiệu quả để chọn chủ đề tiếp theo.

    @param data nội dung insights, mặc định đọc file
    @param top số clip in ra trong bảng xếp hạng
    @returns các dòng đã format
    """
    data = data if data is not None else load_insights()
    videos = list((data.get("videos") or {}).values())
    if not videos:
        return ["chưa có dữ liệu insights — chạy: python3 src/insights.py refresh"]

    key = configured_metrics()[0]
    ranked = sorted(videos, key=lambda item: metric_value((item.get("metrics") or {}).get(key)),
                    reverse=True)
    lines = [f"xếp hạng theo {key}:"]
    for item in ranked[:top]:
        value = metric_value((item.get("metrics") or {}).get(key))
        lines.append(f"  {value:>10.0f}  {item.get('clip_id')}  [{item.get('topic') or 'không rõ chủ đề'}]")

    by_topic: dict[str, list[float]] = {}
    for item in videos:
        topic = str(item.get("topic") or "không rõ chủ đề")
        by_topic.setdefault(topic, []).append(
            metric_value((item.get("metrics") or {}).get(key))
        )
    if by_topic:
        lines.append("trung bình theo chủ đề:")
        for topic, values in sorted(by_topic.items(), key=lambda kv: -sum(kv[1]) / len(kv[1])):
            lines.append(f"  {sum(values) / len(values):>10.0f}  ({len(values)} clip)  {topic}")
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(description="Reels Insights")
    parser.add_argument("cmd", choices=("refresh", "summary"), nargs="?", default="summary")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="số Reel gần nhất")
    args = parser.parse_args()

    if args.cmd == "refresh":
        refresh(args.limit)
        return
    for line in summary():
        print(line)


if __name__ == "__main__":
    main()
