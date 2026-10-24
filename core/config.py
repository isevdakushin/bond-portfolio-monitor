"""Чтение файла настроек config.yaml."""
from __future__ import annotations

import copy
from pathlib import Path

import yaml

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"


def load_config(path: str | Path | None = None) -> dict:
    with open(path or DEFAULT_CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def merged(cfg: dict, overrides: dict | None) -> dict:
    """Копия конфига с переопределёнными значениями (вложенные словари сливаются)."""
    out = copy.deepcopy(cfg)

    def _merge(dst, src):
        for k, v in src.items():
            if isinstance(v, dict) and isinstance(dst.get(k), dict):
                _merge(dst[k], v)
            else:
                dst[k] = v

    _merge(out, overrides or {})
    return out
