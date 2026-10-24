"""Шаг 8: отчёт. Главная проверка — в тексте нет чисел, которых нет в расчёте (реестре) или в цитатах."""
import datetime as dt
import re
from pathlib import Path

import pytest

from core import market
from core.config import load_config
from core.loader import load_snapshot
from core.pipeline import run
from core.report import build
from tests.synthetic import build_next_month

DATA = Path(__file__).resolve().parent / "data"
FILE = Path(__file__).resolve().parent.parent / "Main_Portfolio_14_09_26_v2.xlsx"
pytestmark = pytest.mark.skipif(not FILE.exists(), reason="нет файла портфеля")
TODAY = dt.date(2026, 10, 24)


class _Resp:
    def __init__(self, text):
        self.text = text


@pytest.fixture
def offline(monkeypatch, tmp_path):
    monkeypatch.setattr(market, "CACHE_DIR", tmp_path)

    def fake(url, params=None, timeout=30, retries=3):
        if "treasury" in url and "/2026/" in url:
            return _Resp((DATA / "treasury_2026_sample.csv").read_text(encoding="utf-8"))
        if "fredgraph" in url:
            return _Resp((DATA / "fred_BAMLH0A0HYM2_sample.csv").read_text(encoding="utf-8"))
        raise market.MarketDataError("нет в тесте")

    monkeypatch.setattr(market, "_fetch", fake)


def _numbers_outside_registry(rep):
    text = re.sub(r"\b\d{2}\.\d{2}(?:\.\d{4})?\b", " ", rep.text)          # даты
    quotes = " ".join(rep.quotes)
    bad = []
    for tok in re.findall(r"\d+(?:,\d+)?", text):
        if tok in rep.numbers or tok in quotes:
            continue
        bad.append(tok)
    return bad


def test_report_single_snapshot(offline):
    res = run(load_snapshot(FILE), load_config(), today=TODAY)
    rep = build(res)
    assert rep.version == "шаблон"
    for part in ("Главное", "Портфель и цели", "Рыночный фон", "Риски и сигналы", "Предложения по ребалансировке",
                 "Решение остаётся за аналитиком"):
        assert part in rep.text
    assert "5,58%" in rep.text and "Увеличить IBHJ" in rep.text
    # Номера позиций (#19) и названия правил/бумаг — допустимы; остальное — только из реестра расчёта и цитат.
    bad = [t for t in _numbers_outside_registry(rep) if not re.search(rf"#{t}\b", rep.text)]
    assert bad == [], f"числа вне расчёта: {bad}"


def test_report_with_previous_snapshot(offline):
    cur = load_snapshot(build_next_month(), name="Main_Portfolio_14_10_26.xlsx")
    res = run(cur, load_config(), prev=load_snapshot(FILE), today=TODAY)
    rep = build(res)
    assert "Изменения с 14.09.2026" in rep.text
    assert "Adani" in rep.text and "переход из IG в HY" in rep.text
    bad = [t for t in _numbers_outside_registry(rep) if not re.search(rf"#{t}\b", rep.text)]
    assert bad == [], f"числа вне расчёта: {bad}"


def test_injected_number_is_detected(offline):
    rep = build(run(load_snapshot(FILE), load_config(), today=TODAY))
    rep.text += " Доходность вырастет до 7,77%."        # «выдуманное» число
    assert "7,77" in _numbers_outside_registry(rep)


def test_html_is_self_contained(offline):
    rep = build(run(load_snapshot(FILE), load_config(), today=TODAY))
    assert rep.html.startswith("<!doctype html>") and "@media print" in rep.html
    assert "http://" not in rep.html.split("<main>")[0] and "<script src" not in rep.html
