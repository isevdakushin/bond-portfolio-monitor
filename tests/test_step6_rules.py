"""Шаг 6: правила добора — разбор из файла и проверка по рыночным данным (без интернета)."""
import copy
import datetime as dt
from pathlib import Path

import pytest

from core import market
from core.config import load_config
from core.loader import load_snapshot
from core.rules import evaluate, parse_rules

DATA = Path(__file__).resolve().parent / "data"
FILE = Path(__file__).resolve().parent.parent / "Main_Portfolio_14_09_26_v2.xlsx"
pytestmark = pytest.mark.skipif(not FILE.exists(), reason="нет файла портфеля")
TODAY = dt.date(2026, 10, 24)


class _Resp:
    def __init__(self, text):
        self.text = text


@pytest.fixture
def mk(monkeypatch, tmp_path):
    monkeypatch.setattr(market, "CACHE_DIR", tmp_path)

    def fake(url, params=None, timeout=30, retries=3):
        if "treasury" in url and "/2026/" in url:
            return _Resp((DATA / "treasury_2026_sample.csv").read_text(encoding="utf-8"))
        if "fredgraph" in url:
            return _Resp((DATA / "fred_BAMLH0A0HYM2_sample.csv").read_text(encoding="utf-8"))
        raise market.MarketDataError("нет в тесте")

    monkeypatch.setattr(market, "_fetch", fake)
    return market.load_market(dt.date(2026, 9, 14), None, today=TODAY, history_days=20)


@pytest.fixture(scope="module")
def snap():
    return load_snapshot(FILE)


def test_rules_parsed_from_file(snap):
    r = parse_rules(snap, load_config())
    assert [(s["lower"], s["target_after"]) for s in r.ladder] == [(pytest.approx(0.051), pytest.approx(0.20)),
                                                                  (pytest.approx(0.0525), pytest.approx(0.25))]
    assert (r.hy_trigger_bp, r.hy_instrument, r.hy_from, r.hy_to) == (325, "IBHJ", pytest.approx(0.045), pytest.approx(0.08))
    assert r.calendar == [(11, 15), (1, 15)]
    assert all(v.startswith("файл") for v in r.source.values())


def test_changed_levels_in_new_file_are_used(snap):
    s = copy.deepcopy(snap)
    s.dashboard_texts = [t.replace("5,10–5,15%", "5,20–5,25%").replace("≥325", "≥350") for t in s.dashboard_texts]
    s.sheet_texts = {}
    r = parse_rules(s, load_config())
    assert r.ladder[0]["lower"] == pytest.approx(0.052) and r.hy_trigger_bp == 350


def test_fallback_to_config(snap):
    s = copy.deepcopy(snap)
    s.dashboard_texts, s.sheet_texts = [], {}
    r = parse_rules(s, load_config())
    assert r.hy_trigger_bp == 325 and "config.yaml" in r.source["ladder"]


def test_evaluation_on_market_data(snap, mk):
    ev = evaluate(snap, mk, load_config(), today=TODAY)
    hy = ev["table"][ev["table"]["Правило"].str.startswith("HY OAS")].iloc[0]
    assert hy["Статус"] == "сработало сейчас" and "14.10.2026" in hy["Касание с даты портфеля"]
    ladder = ev["table"][ev["table"]["Правило"].str.startswith("Лестница")]
    assert list(ladder["Статус"]) == ["уровень достигался, сейчас ниже"] * 2
    assert "23.09.2026" in ladder.iloc[0]["Касание с даты портфеля"] and "29.09.2026" in ladder.iloc[1]["Касание с даты портфеля"]
    top = ev["actions"][0]
    assert top["Действие"].startswith("Довести IBHJ с 4,5% до 8%") and "Medium IG" in top["Финансирование"]
    assert ev["next_reviews"][0] == dt.date(2026, 11, 15)


def test_already_executed_rule(snap, mk):
    s = copy.deepcopy(snap)
    p = s.positions
    p.loc[p["issuer"].str.contains("IBHJ"), "weight"] = 0.08
    ev = evaluate(s, mk, load_config(), today=TODAY)
    hy = ev["table"][ev["table"]["Правило"].str.startswith("HY OAS")].iloc[0]
    assert hy["Статус"].startswith("выполнено")
    assert not any("IBHJ" in a["Действие"] for a in ev["actions"])
