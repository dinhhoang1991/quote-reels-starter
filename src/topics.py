#!/usr/bin/env python3
"""Xoay vòng chủ đề: biết chủ đề nào đã dùng gần đây để chọn cái tiếp theo.

    python3 src/topics.py list     # chủ đề, số lần đã đăng, lần cuối
    python3 src/topics.py next     # in chủ đề nên làm tiếp (ít dùng nhất, lâu nhất)
"""

from __future__ import annotations

import argparse

from config import Cfg, load_config
from logutil import PUBLISHED_STATE, load_log, log

# Dùng khi config.yaml không khai báo `content.topics`.
DEFAULT_TOPICS = (
    "Phân loại tài sản theo thu nhập mỗi tháng",
    "Cách sống đẳng cấp sau tuổi 30",
    "Cách gia tăng tài sản theo số vốn",
    "Kiểu người dễ thành công",
    "Cổ nhân nói về đạo vợ chồng",
    "Thói quen người giàu làm mỗi sáng",
    "Sai lầm tài chính trước tuổi 35",
    "Dấu hiệu bạn đang dừng ở tầng trung lưu",
)


def configured_topics(cfg: Cfg | None = None) -> list[str]:
    """Danh sách chủ đề xoay vòng trong config.

    @param cfg config, mặc định đọc config.yaml
    @returns danh sách chủ đề, rỗng nếu config khai báo list rỗng
    """
    cfg = cfg or load_config()
    content_cfg = cfg.get("content", {}) or {}
    configured = content_cfg.get("topics")
    if configured is None:
        return list(DEFAULT_TOPICS)
    return [str(topic).strip() for topic in configured if str(topic).strip()]


def topic_usage() -> dict[str, dict[str, object]]:
    """Thống kê chủ đề đã đăng, đọc từ data/published.json.

    @returns {topic: {"count": n, "last_ts": ts, "last_iso": iso}}
    """
    usage: dict[str, dict[str, object]] = {}
    for post in load_log().get("posts", []):
        if str(post.get("state", "")).upper() != PUBLISHED_STATE:
            continue
        topic = str(post.get("topic") or "").strip()
        if not topic:
            continue
        entry = usage.setdefault(topic, {"count": 0, "last_ts": 0.0, "last_iso": ""})
        entry["count"] = int(entry["count"]) + 1
        ts = float(post.get("ts", 0))
        if ts > float(entry["last_ts"]):
            entry["last_ts"] = ts
            entry["last_iso"] = str(post.get("iso", ""))
    return usage


def topic_scores(cfg: Cfg | None = None) -> dict[str, float]:
    """Điểm hiệu quả trung bình theo chủ đề từ `data/insights.json`.

    Đọc insights ở đây (import muộn) để `topics.py` vẫn dùng được khi thiếu `requests`.

    @param cfg config, mặc định đọc config.yaml
    @returns {chủ đề: điểm}; rỗng khi chưa có dữ liệu insights
    """
    try:
        from insights import topic_scores as scores

        return scores()
    except (SystemExit, Exception):
        return {}


def next_topic(cfg: Cfg | None = None) -> str | None:
    """Chủ đề nên làm tiếp: dùng ít nhất và lâu nhất, cùng hạng thì chọn điểm cao hơn.

    Vòng xoay vẫn đảm bảo phủ đều (số lần dùng là tiêu chí đầu); điểm insights chỉ
    phá hoà giữa các chủ đề ngang số lần, để chủ đề hiệu quả hơn được làm trước.

    @param cfg config, mặc định đọc config.yaml
    @returns tên chủ đề, None nếu config không có chủ đề nào
    """
    topics = configured_topics(cfg)
    if not topics:
        return None
    usage = topic_usage()
    scores = topic_scores(cfg)
    return min(
        topics,
        key=lambda topic: (
            int(usage.get(topic, {}).get("count", 0)),
            -float(scores.get(topic, 0.0)),
            float(usage.get(topic, {}).get("last_ts", 0.0)),
        ),
    )


def rotation_report(cfg: Cfg | None = None) -> list[tuple[str, int, str, float]]:
    """Bảng xoay vòng để in ra hoặc cho doctor đọc.

    @param cfg config, mặc định đọc config.yaml
    @returns [(chủ đề, số lần đăng, lần cuối ISO, điểm insights)] theo thứ tự đã khai báo
    """
    usage = topic_usage()
    scores = topic_scores(cfg)
    report = []
    for topic in configured_topics(cfg):
        entry = usage.get(topic, {})
        report.append(
            (
                topic,
                int(entry.get("count", 0)),
                str(entry.get("last_iso", "")) or "chưa đăng",
                float(scores.get(topic, 0.0)),
            )
        )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Xoay vòng chủ đề Reels")
    parser.add_argument("cmd", choices=("list", "next"), nargs="?", default="list")
    args = parser.parse_args()

    if args.cmd == "next":
        topic = next_topic()
        if topic is None:
            raise SystemExit("config.yaml chưa khai báo content.topics")
        print(topic)
        return
    for topic, count, last, score in rotation_report():
        log(f"{count:>2} lần | {last:<20} | điểm {score:>8.0f} | {topic}")


if __name__ == "__main__":
    main()
