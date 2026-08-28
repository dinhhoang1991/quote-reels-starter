#!/usr/bin/env python3
"""Load config.yaml once. Every renderer reads this — never hardcode style/audio."""

from __future__ import annotations

from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


class Cfg:
    """Nested dict with attribute access and .get()."""

    def __init__(self, data: dict[str, Any]):
        self._data = data
        for key, value in data.items():
            object.__setattr__(self, key, Cfg(value) if isinstance(value, dict) else value)

    def get(self, key: str, default: Any = None) -> Any:
        value = self._data.get(key, default)
        if isinstance(value, dict) and not isinstance(value, Cfg):
            return Cfg(value)
        return value

    def __getitem__(self, key: str) -> Any:
        return self.get(key)


_cache: Cfg | None = None


def load_config(path: Path | None = None) -> Cfg:
    global _cache
    if _cache is not None and path is None:
        return _cache
    try:
        import yaml
    except ImportError as exc:
        raise SystemExit("Chưa cài pyyaml. Chạy: pip install pyyaml") from exc
    cfg_path = path or (ROOT / "config.yaml")
    if not cfg_path.exists():
        raise SystemExit(f"Không thấy {cfg_path}")
    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    cfg = Cfg(raw)
    if path is None:
        _cache = cfg
    return cfg


def root() -> Path:
    return ROOT


def resolve_path(rel: str) -> Path:
    p = Path(rel)
    return p if p.is_absolute() else ROOT / p
