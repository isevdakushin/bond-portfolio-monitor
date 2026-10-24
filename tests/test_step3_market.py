"""Шаг 3: рыночные данные. Без интернета — на сохранённых выгрузках Treasury и FRED (tests/data)."""
import datetime as dt
from pathlib import Path

import pytest

from core import market
from core.loader import load_snapshot

DATA = Path(__file__).resolve().parent / "data"
FILE = Path(__file__).resolve().parent.parent / "Main_Portfolio_14_09_26_v2.xlsx"


class _Resp:
    def __init__(self, text):
        self.text = text

    def json(self):
        import json
        return json.loads(self.text)


@pytest.fixture
def offline(monkeypatch, tmp_path):
    """Подменяет сетевые запросы сохранёнными файлами; кэш — во временной папке."""
    monkeypatch.setattr(market, "CACHE_DIR", tmp_path)
    calls = []

    def fake_fetch(url, params=None, timeout=30, retries=3):
        calls.append(url)
        if "treasury" in url:
            if "/2026/" not in url:
                raise market.MarketDataError("нет данных за год")
            return _Resp((DATA / "treasury_2026_sample.csv").read_text(encoding="utf-8"))
        if "fredgraph" in url:
            return _Resp((DATA / "fred_BAMLH0A0HYM2_sample.csv").read_text(encoding="utf-8"))
        raise market.MarketDataError("FRED API недоступен в тесте")

    monkeypatch.setattr(market, "_fetch", fake_fetch)
    return calls


def test_tenor_parsing():
    assert market.tenor_years("1 Mo") == pytest.approx(1 / 12)
    assert market.tenor_years("1.5 Month") == pytest.approx(0.125)
    assert market.tenor_years("10 Yr") == 10


def test_curve_on_snapshot_date(offline):
    c = market.ust_curve(dt.date(2026, 9, 14))
    assert c.date == dt.date(2026, 9, 14)
    assert c.at(10) == pytest.approx(0.0497)          # Treasury, 14.09.2026
    assert c.at(2) == pytest.approx(0.0465)


def test_weekend_uses_previous_business_day(offline):
    c = market.ust_curve(dt.date(2026, 9, 13))         # воскресенье
    assert c.date == dt.date(2026, 9, 11)


def test_linear_interpolation_and_flat_extrapolation():
    tenors, ys = [1, 2, 5, 10], [0.04, 0.045, 0.048, 0.05]
    assert market.interpolate(tenors, ys, 7.5) == pytest.approx(0.049)    # между 5Y и 10Y
    assert market.interpolate(tenors, ys, 0.5) == pytest.approx(0.04)     # ниже — плоско
    assert market.interpolate(tenors, ys, 40) == pytest.approx(0.05)      # выше — плоско


def test_hy_oas_in_bp_with_csv_fallback(offline):
    s, meta = market.hy_oas_series(api_key=None, start=dt.date(2026, 8, 1))
    assert meta["channel"].startswith("FRED CSV")
    assert market.value_on(s, dt.date(2026, 9, 14)) == (dt.date(2026, 9, 14), pytest.approx(271.0))


def test_cache_used_when_source_fails(offline, monkeypatch):
    market.ust_curve(dt.date(2026, 9, 14))             # наполняем кэш
    monkeypatch.setattr(market, "_fetch", lambda *a, **k: (_ for _ in ()).throw(market.MarketDataError("сбой")))
    (market.CACHE_DIR / "treasury_2026.csv.json").write_text('{"fetched_at": "2000-01-01T00:00:00"}', encoding="utf-8")
    c = market.ust_curve(dt.date(2026, 9, 14))
    assert c.how == "кэш (источник недоступен)" and c.at(10) == pytest.approx(0.0497)


def test_no_data_no_value(offline, monkeypatch):
    monkeypatch.setattr(market, "_fetch", lambda *a, **k: (_ for _ in ()).throw(market.MarketDataError("сбой")))
    m = market.load_market(dt.date(2026, 9, 14), None, today=dt.date(2026, 10, 23))
    assert m.curve_asof is None and m.hy_asof is None and len(m.errors) >= 2


@pytest.mark.skipif(not FILE.exists(), reason="нет файла портфеля")
def test_dashboard_10y_claim_checked(offline):
    snap = load_snapshot(FILE)
    m = market.load_market(snap.as_of, None, today=dt.date(2026, 10, 23))
    claims = market.check_file_claims(snap.dashboard_texts, m)
    assert len(claims) == 1                              # «10Y UST 4,94%»; уровни лестницы не считаются фактом
    assert claims[0]["stated"] == pytest.approx(0.0494)
    assert claims[0]["diff_bp"] == pytest.approx(-3.0)
    assert m.hy_latest[1] == pytest.approx(340.0)        # 22.10.2026
