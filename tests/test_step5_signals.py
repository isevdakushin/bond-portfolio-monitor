"""Шаг 5: сигналы по спредам и сопоставление позиций двух снимков."""
import copy
from pathlib import Path

import pytest

from core.compare import match_positions
from core.config import load_config, merged
from core.loader import load_snapshot
from core.signals import relative_value, spread_changes

FILE = Path(__file__).resolve().parent.parent / "Main_Portfolio_14_09_26_v2.xlsx"
pytestmark = pytest.mark.skipif(not FILE.exists(), reason="нет файла портфеля")


@pytest.fixture(scope="module")
def snap():
    return load_snapshot(FILE)


def _sig(rv, name):
    return rv[rv["Позиция"].str.contains(name, regex=False)].iloc[0]


def test_wide_signals_on_real_file(snap):
    rv = relative_value(snap, load_config())
    wide = set(rv.loc[rv["Сигнал"] == "шире группы", "Позиция"])
    assert {"#19 Ecopetrol", "#23 Meta Platforms", "#13 Adani Ports & SEZ", "#3 Oracle"} == wide
    assert "мало пиров" in _sig(rv, "Ecopetrol")["Надёжность"]
    assert rv.attrs["slope"] > 0


def test_duration_adjustment_removes_short_bond_artifacts(snap):
    on = relative_value(snap, load_config())
    off = relative_value(snap, merged(load_config(), {"signals": {"duration_adjust": False}}))
    assert _sig(off, "Equinix")["Сигнал"] == "уже группы"     # без поправки — ложный сигнал
    assert _sig(on, "Equinix")["Сигнал"] == "в норме"


def test_peers_exclude_self_and_same_group(snap):
    r = _sig(relative_value(snap, load_config()), "Adani")
    assert "Adani" not in r["Пиры"] and r["Группа"] == "BBB" and r["Пиров"] == 6


def test_threshold_configurable(snap):
    rv = relative_value(snap, merged(load_config(), {"signals": {"wide_abs_bp": 100}}))
    assert set(rv.loc[rv["Сигнал"] == "шире группы", "Позиция"]) == {"#19 Ecopetrol"}


def _modified(snap, changes, drop=(), rename=None):
    s = copy.deepcopy(snap)
    p = s.positions
    for name, d in changes.items():
        p.loc[p["issuer"].str.contains(name, regex=False), "gspread"] += d
    if drop:
        p = p[~p["issuer"].str.contains("|".join(drop))]
    if rename:
        for old, new in rename.items():
            m = p["issuer"].str.contains(old, regex=False)
            p.loc[m, "issuer"] = new
            p.loc[m, "isin"] = None
    s.positions = p.reset_index(drop=True)
    return s


def test_matching_new_removed_and_by_name(snap):
    cur = _modified(snap, {}, drop=("Equinix",))
    cur.positions.loc[cur.positions["issuer"].str.contains("Oracle"), "isin"] = None   # без ISIN — по названию
    m = match_positions(cur.positions, snap.positions)
    assert (m["Статус"] == "выбыла").sum() == 1 and m.loc[m["Статус"] == "выбыла", "label"].iloc[0] == "#1 Equinix"
    assert m.loc[m["issuer"].fillna("").str.startswith("Oracle"), "match_by"].iloc[0] == "название"
    assert (m["Статус"] == "осталась").sum() == 27


def test_abnormal_widening_vs_group(snap):
    # Все бумаги расширились на 10 бп, Adani — на 60 бп: аномалия только у Adani.
    changes = {n: 10 for n in snap.positions["issuer"]}
    cur = _modified(snap, changes)
    cur.positions.loc[cur.positions["issuer"].str.contains("Adani"), "gspread"] += 50
    ch = spread_changes(cur, snap, load_config())
    flagged = ch.loc[ch["Сигнал"] == "аномальное расширение", "Позиция"].tolist()
    assert flagged == ["#13 Adani Ports & SEZ"]
    assert ch.loc[ch["Позиция"] == "#13 Adani Ports & SEZ", "Сверх группы, бп"].iloc[0] == pytest.approx(50)
