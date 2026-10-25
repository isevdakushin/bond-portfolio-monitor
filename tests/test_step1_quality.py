"""Шаг 1: загрузка и проверки качества на реальном файле компании + устойчивость парсера."""
import datetime as dt
import io
from pathlib import Path

import pytest
from openpyxl import load_workbook

from core.bondmath import isin_valid, modified_duration
from core.config import load_config
from core.loader import load_snapshot
from core.quality import run_checks

ROOT = Path(__file__).resolve().parent.parent
FILE = ROOT / "Main_Portfolio_14_09_26_v2.xlsx"
pytestmark = pytest.mark.skipif(not FILE.exists(), reason="нет файла портфеля")


@pytest.fixture(scope="module")
def snap():
    return load_snapshot(FILE)


@pytest.fixture(scope="module")
def issues(snap):
    return run_checks(snap, load_config())


def test_loads_positions_and_excludes_totals(snap):
    p = snap.positions
    assert len(p) == 28
    assert snap.file_totals["excel_row"] == 35
    assert abs(p["weight"].sum() - 1) < 1e-9
    assert snap.as_of == dt.date(2026, 9, 14)


def test_weighted_metrics_match_excel_formulas(snap):
    p = snap.positions
    assert abs((p.weight * p.ytm).sum() - 0.0557805) < 1e-9          # '01 Portfolio'!H35
    assert abs((p.weight * p.gspread).sum() - 108.155) < 1e-6        # I35
    assert abs((p.weight * p.mod_duration).sum() - 4.73245) < 1e-6   # J35


def test_dashboard_and_allocation_parsed(snap):
    m = snap.dashboard_metrics
    assert abs(m["ytm_gross"]["target"] - 0.0593005) < 1e-9
    assert abs(m["mod_duration"]["target"] - 6.46045) < 1e-9
    a = snap.allocation.set_index("sleeve")
    assert a.loc["ST Corp Bonds", "portfolio_sleeve"] == "ST Corp Bonds IG"
    assert a.loc["ST Corp Bonds", "match"] == "частичное"


def _has(issues, level, category, position_part=""):
    m = (issues["Уровень"] == level) & (issues["Категория"] == category)
    if position_part:
        m &= issues["Позиция"].str.contains(position_part, regex=False)
    return m.any()


def test_known_issues_found(issues):
    assert _has(issues, "Ошибка", "Погашение", "Targa")
    assert _has(issues, "Ошибка", "Рейтинг / категория", "AbbVie")
    assert _has(issues, "Предупреждение", "Страна", "Millicom")
    assert _has(issues, "Предупреждение", "Страна", "ArcelorMittal")
    assert _has(issues, "Предупреждение", "Дашборд")   # целевая дюрация 6,46 vs текст 5,5–5,8


def test_targa_duration_points_to_name_date(issues):
    row = issues[(issues["Категория"] == "Погашение") & issues["Позиция"].str.contains("Targa")].iloc[0]
    assert "ближе к дате из названия" in row["Комментарий"]


def test_no_false_alarms_on_consistent_parts(issues):
    errors = issues[issues["Уровень"] == "Ошибка"]
    assert not errors["Категория"].isin(["Веса", "Итоги листа портфеля", "Breakdown", "Купон", "Дашборд"]).any()
    # T-bills без ISIN — не ошибка формата ISIN
    assert not _has(issues, "Предупреждение", "ISIN", "T-bills")
    # «EM всего (вкл. EM HY ...)» сверяется как EM (28%), а не как HY
    assert issues["Комментарий"].str.contains("EM всего (Region = EM) = 28,0%", regex=False).any()


def test_adani_duration_exceeds_maturity(issues):
    assert _has(issues, "Ошибка", "Дюрация", "Adani")
    assert _has(issues, "Предупреждение", "Дюрация", "Equinix")


def test_isin_checksum():
    assert isin_valid("US0378331005")      # Apple Inc. common stock
    assert not isin_valid("US0378331006")


def test_duration_formula_sanity():
    # 10-летний par-бонд 5%: mod. duration ≈ 7,8
    md = modified_duration(dt.date(2026, 1, 1), dt.date(2036, 1, 1), 0.05, 0.05)
    assert 7.7 < md < 7.9


def test_parser_robust_to_shifted_layout(tmp_path):
    """Сдвигаем таблицу портфеля на 3 строки и меняем порядок листов — парсер должен работать."""
    wb = load_workbook(FILE)
    wb["01 Portfolio"].insert_rows(1, amount=3)
    wb.move_sheet("01 Portfolio", offset=2)
    buf = io.BytesIO()
    wb.save(buf)
    s = load_snapshot(buf.getvalue(), name="shifted.xlsx")
    assert len(s.positions) == 28
    assert abs((s.positions.weight * s.positions.ytm).sum() - 0.0557805) < 1e-9
