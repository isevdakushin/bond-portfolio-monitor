"""Шаг 4: независимый пересчёт G-spread (без интернета — на сохранённой выгрузке Treasury)."""
import datetime as dt
from pathlib import Path

import pytest

from core import market
from core.config import load_config
from core.gspread import recompute
from core.loader import load_snapshot

DATA = Path(__file__).resolve().parent / "data"
FILE = Path(__file__).resolve().parent.parent / "Main_Portfolio_14_09_26_v2.xlsx"
pytestmark = pytest.mark.skipif(not FILE.exists(), reason="нет файла портфеля")


@pytest.fixture(scope="module")
def res():
    curves = market.parse_treasury_csv((DATA / "treasury_2026_sample.csv").read_text(encoding="utf-8"))
    snap = load_snapshot(FILE)
    row = curves.loc[snap.as_of]
    curve = market.Curve(requested=snap.as_of, date=snap.as_of, tenors=list(row.index), yields=list(row.values))
    return recompute(snap, curve, load_config(), curves)


def _row(res, name):
    t = res.table
    return t[t["Позиция"].str.contains(name, regex=False)].iloc[0]


def test_formula_by_maturity(res):
    r = _row(res, "Equinix")                 # YTM 4,74%, погашение 15.07.2027 (0,83 года)
    assert r["G (срок)"] == pytest.approx((0.0474 - r["UST на срок"]) * 1e4)
    assert r["Срок, лет"] == pytest.approx(0.832, abs=0.01)


def test_funds_and_cash_not_recomputed(res):
    assert "ETF" in _row(res, "IBHJ")["Комментарий"]
    assert "денежная" in _row(res, "T-bills")["Комментарий"]
    assert _row(res, "IBHJ")["Статус"] == "—"


def test_targa_both_dates(res):
    r = _row(res, "Targa")
    assert "15.04.2031" in r["Комментарий"] and r["G (срок, дата из названия)"] == pytest.approx(65, abs=1)


def test_systematic_offset_detected(res):
    assert res.systematic["flag"] and res.systematic["median"] == pytest.approx(-30.7, abs=0.5)


def test_outliers_relative_to_common_offset(res):
    outliers = res.table[res.table["Статус"] == "выброс"]["Позиция"].tolist()
    assert outliers == ["#1 Equinix"]


def test_implied_curve_date_duration_method(res):
    imp = res.implied["duration"]
    assert imp["date"] == dt.date(2026, 9, 3) and imp["mae"] < 4


def test_portfolio_level(res):
    assert res.portfolio["file"] == pytest.approx(108.155)
    assert res.portfolio["recomputed"] == pytest.approx(82.5, abs=0.5)
