"""Шаг 2: аналитика портфеля на реальном файле."""
from pathlib import Path

import pytest

from core.analytics import analyze
from core.config import load_config, merged
from core.loader import load_snapshot

FILE = Path(__file__).resolve().parent.parent / "Main_Portfolio_14_09_26_v2.xlsx"
pytestmark = pytest.mark.skipif(not FILE.exists(), reason="нет файла портфеля")


@pytest.fixture(scope="module")
def an():
    return analyze(load_snapshot(FILE), load_config())


def test_metrics_match_dashboard(an):
    m = an.metrics
    assert abs(m["ytm_gross"] - 0.0557805) < 1e-9     # 00 Dashboard B4
    assert abs(m["ytm_net"] - 0.0507805) < 1e-9       # B5
    assert abs(m["mod_duration"] - 4.73245) < 1e-9    # B6
    assert abs(m["gspread"] - 108.155) < 1e-9         # B7
    assert abs(m["hy_share"] - 0.12) < 1e-9 and abs(m["em_share"] - 0.28) < 1e-9


def test_targets_gaps(an):
    t = an.targets.set_index("key")
    assert abs(t.loc["ytm_gross", "Отклонение"] - (0.0557805 - 0.0593005)) < 1e-9
    assert t.loc["mod_duration", "Статус"] == "ниже цели"


def test_allocation_matches_sheet(an):
    a = an.allocation.set_index("Sleeve")
    assert abs(a.loc["Long Term Corp Bonds IG", "Отклонение"] + 0.10) < 1e-9
    assert a.loc["Cash/ T-bills", "Статус"] == "перевес"
    assert a.loc["ST Corp Bonds", "Статус"] == "в пределах"


def test_proforma_duration_in_dashboard_text_range(an):
    assert 5.5 <= an.proforma["mod_duration"] <= 5.8


def test_concentration(an):
    c = an.concentration
    energy = c[(c["Тип"] == "Сектор (укрупнённо)") & (c["Объект"] == "Energy")].iloc[0]
    assert abs(energy["Доля"] - 0.225) < 1e-9 and energy["Статус"] == "превышен"
    hy = c[c["Объект"].str.startswith("HY всего")].iloc[0]
    assert abs(hy["Лимит"] - 0.155) < 1e-9            # 7,5% + 8% из листа Allocation
    assert (c.loc[c["Тип"] == "Эмитент", "Статус"] == "в пределах").all()


def test_thresholds_configurable():
    cfg = merged(load_config(), {"concentration": {"issuer_max": 0.03}})
    c = analyze(load_snapshot(FILE), cfg).concentration
    assert (c.loc[c["Тип"] == "Эмитент", "Статус"] == "превышен").sum() == 14   # все бумаги по 3,5%


def test_breakdown_weights_sum_to_one(an):
    for name, df in an.breakdowns.items():
        assert abs(df["Вес"].sum() - 1) < 1e-9, name
