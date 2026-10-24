"""Шаг 9: оценка на текущую дату по индексам FRED и стресс-тест (без интернета — tests/data)."""
import datetime as dt
from pathlib import Path

import pytest

from core import market
from core.config import load_config, merged
from core.estimate import estimate, index_bucket
from core.loader import load_snapshot
from core.stress import run_all, run_scenario

DATA = Path(__file__).resolve().parent / "data"
FILE = Path(__file__).resolve().parent.parent / "Main_Portfolio_14_09_26_v2.xlsx"
pytestmark = pytest.mark.skipif(not FILE.exists(), reason="нет файла портфеля")


@pytest.fixture(scope="module")
def snap():
    return load_snapshot(FILE)


def _loader(sid):
    text = (DATA / f"fred_{sid}_sample.csv").read_text(encoding="utf-8")
    return market.parse_fred_csv(text), {"url": "", "how": "тест", "fetched_at": None, "channel": "тест"}


@pytest.fixture(scope="module")
def curves():
    df = market.parse_treasury_csv((DATA / "treasury_2026_sample.csv").read_text(encoding="utf-8"))

    def c(d):
        row = df.loc[d]
        return market.Curve(requested=d, date=d, tenors=list(row.index), yields=list(row.values))
    return c


def test_index_mapping(snap):
    b = dict(zip(snap.positions["label"], snap.positions.apply(index_bucket, axis=1)))
    assert b["#19 Ecopetrol"] == "EM_HY" and b["#6 PETRONAS"] == "EM_IG" and b["#25 Apple"] == "AA"
    assert b["#3 Oracle"] == "BBB" and b["#27 IBHJ - iShares iBonds 2030 Term HY ETF"] == "HY_FUND"
    assert not isinstance(b["#28 US T-bills 2026"], str)          # кэш — без индекса, только UST


def test_estimate_formula_and_portfolio(snap, curves, monkeypatch):
    monkeypatch.setattr("core.estimate.ust_curve", lambda d: curves(d))
    e = estimate(snap, load_config(), None, curves(dt.date(2026, 9, 14)), curves(dt.date(2026, 10, 23)),
                 today=dt.date(2026, 10, 24), series_loader=_loader)
    assert e.est_date == dt.date(2026, 10, 22)                   # последняя дата, общая для кривой и индексов
    idx = e.indices.set_index("Группа")
    assert idx.loc["EM_HY", "Δ OAS, бп"] == pytest.approx(85) and idx.loc["HY_FUND", "Δ OAS, бп"] == pytest.approx(69)
    pos = e.positions.set_index("Позиция")
    r = pos.loc["#19 Ecopetrol"]
    assert r["Δ YTM (оценка), бп"] == pytest.approx(r["Δ UST, бп"] + 85)
    assert r["Ценовой эффект"] == pytest.approx(-4.44 * r["Δ YTM (оценка), бп"] / 1e4)
    assert pos.loc["#28 US T-bills 2026", "Δ OAS индекса, бп"] == 0
    p = e.portfolio
    assert p["total"] == pytest.approx(p["price"] + p["carry"])
    assert p["gs_est"] - p["gs_file"] == pytest.approx(p["d_oas_bp"])


def test_stress_ust_parallel(snap):
    d = run_scenario(snap, {"name": "x", "ust": 100})
    # −MD × 1% по каждой позиции: P&L портфеля = −(средневзвешенная дюрация) × 1%
    assert d["P&L, % NAV"].sum() == pytest.approx(-4.73245 / 100)


def test_stress_spreads_no_double_count(snap):
    d = run_scenario(snap, {"name": "x", "em": 100, "hy": 150}).set_index("Позиция")
    assert d.loc["#19 Ecopetrol", "Δ спреда, бп"] == 150          # EM и HY — берётся больший шок
    assert d.loc["#6 PETRONAS", "Δ спреда, бп"] == 100
    assert d.loc["#25 Apple", "Δ спреда, бп"] == 0
    assert d.loc["#28 US T-bills 2026", "Δ спреда, бп"] == 0


def test_scenarios_configurable(snap):
    cfg = merged(load_config(), {"stress": {"scenarios": [{"name": "HY +200", "hy": 200}]}})
    s = run_all(snap, cfg)["summary"]
    assert list(s["Сценарий"]) == ["HY +200"]
    hy = snap.positions[snap.positions["rating"].isin(["BB", "BB+", "B+"])]
    assert s.iloc[0]["P&L портфеля, % NAV"] == pytest.approx(-(hy["weight"] * hy["mod_duration"]).sum() * 0.02)
