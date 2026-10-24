"""Шаг 7: сравнение двух снимков (на синтетическом «следующем месяце», см. tests/synthetic.py)."""
import copy
import datetime as dt

import pytest

from core.config import load_config
from core.loader import load_snapshot
from core.market import Curve
from core.snapshots import compare, inherit_targets, order_snapshots
from tests.synthetic import FILE, build_next_month

pytestmark = pytest.mark.skipif(not FILE.exists(), reason="нет файла портфеля")


@pytest.fixture(scope="module")
def pair():
    prev = load_snapshot(FILE)
    cur = load_snapshot(build_next_month(), name="Main_Portfolio_14_10_26.xlsx")
    return cur, prev


def _flat(d, y):
    return Curve(requested=d, date=d, tenors=[0.25, 1, 2, 5, 10, 30], yields=[y] * 6)


def test_dates_and_order(pair):
    cur, prev = pair
    assert cur.as_of == dt.date(2026, 10, 14) and prev.as_of == dt.date(2026, 9, 14)
    c, p, note = order_snapshots(prev, cur)          # перепутанный порядок исправляется
    assert c is cur and "обратном порядке" in note


def test_positions_new_removed_and_flags(pair):
    cmp = compare(*pair, load_config())
    pos = cmp.positions.set_index("Позиция")
    assert (cmp.positions["Статус"] == "новая").sum() == 1 and (cmp.positions["Статус"] == "выбыла").sum() == 1
    assert pos.loc["#9 Pertamina", "Статус"] == "выбыла"
    assert pos.loc["#9 SYNTH TestCo", "Статус"] == "новая"
    adani = pos.loc["#13 Adani Ports & SEZ"]
    assert adani["Δ рейтинга, ступеней"] == 1 and "переход из IG в HY" in adani["Изменения"]
    assert adani["Δ G-spread, бп"] == pytest.approx(70)
    assert pos.loc["#27 IBHJ - iShares iBonds 2030 Term HY ETF", "Δ вес, п.п."] == pytest.approx(3.5)
    assert pos.loc["#25 Apple", "Δ YTM, бп"] == pytest.approx(30)
    assert list(cmp.rating_changes["Позиция"]) == ["#13 Adani Ports & SEZ"]


def test_attribution_sums_to_total_change(pair):
    cur, prev = pair
    cmp = compare(cur, prev, load_config(), _flat(cur.as_of, 0.042), _flat(prev.as_of, 0.040))
    mt = cmp.metrics.set_index("key")
    for key, metric, scale in (("YTM, бп", "ytm_gross", 1e4), ("G-spread, бп", "gspread", 1),
                               ("Mod. duration, лет", "mod_duration", 1)):
        d = cmp.attribution[key].set_index("Компонент")["Вклад"]
        assert d["Итого изменение портфеля"] == pytest.approx(mt.loc[metric, "Изменение"] * scale, abs=1e-6)
        assert d.drop("Итого изменение портфеля").sum() == pytest.approx(d["Итого изменение портфеля"], abs=1e-6)
    ytm = cmp.attribution["YTM, бп"].set_index("Компонент")["Вклад"]
    # рынок: +20 бп по всей кривой × веса оставшихся бумаг (без выбывшей Pertamina 3,5%)
    assert ytm["Рынок UST (сдвиг кривой на сроки бумаг)"] == pytest.approx(20 * (1 - 0.035), abs=1e-6)


def test_allocation_change(pair):
    a = compare(*pair, load_config()).allocation.set_index("Sleeve")
    assert a.loc["HY Funds", "Δ за период"] == pytest.approx(0.035)
    assert a.loc["Cash/ T-bills", "Ранее"] == pytest.approx(0.16) and a.loc["Cash/ T-bills", "Текущее"] == pytest.approx(0.10)


def test_concentration_status_changes(pair):
    cc = compare(*pair, load_config()).concentration_changes
    assert not cc.empty and set(cc.columns) >= {"Статус ранее", "Статус сейчас"}


def test_inherit_targets_when_missing(pair):
    cur, prev = pair
    bare = copy.deepcopy(cur)
    bare.dashboard_metrics, bare.allocation, bare.dashboard_texts, bare.sheet_texts = {}, None, [], {}
    out, notes = inherit_targets(bare, prev)
    assert out.dashboard_metrics["ytm_gross"]["target"] == pytest.approx(0.0593005)
    assert out.dashboard_metrics["ytm_gross"]["current"] is None
    assert out.allocation is not None and len(notes) == 3
