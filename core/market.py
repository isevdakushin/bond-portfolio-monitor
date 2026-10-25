"""Рыночные данные из официальных бесплатных источников.

- Кривая UST: U.S. Department of the Treasury, Daily Treasury Par Yield Curve Rates (CSV по годам).
- HY OAS: FRED (Federal Reserve Bank of St. Louis), серия BAMLH0A0HYM2 —
  ICE BofA US High Yield Index Option-Adjusted Spread. Через официальный API (нужен бесплатный ключ),
  без ключа — через CSV-выгрузку fredgraph (бывает медленной).

Каждое значение хранится с датой наблюдения и источником. Загруженные данные кэшируются в папке cache/:
если источник недоступен, используется последний кэш с явной пометкой. Ничего не подставляется и не
дополняется: нет данных — нет значения.
"""
from __future__ import annotations

import datetime as dt
import io
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
import requests

CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"
UA = {"User-Agent": "Mozilla/5.0 (bond-portfolio-analytics; research use)"}

TREASURY_CSV = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
                "daily-treasury-rates.csv/{year}/all?type=daily_treasury_yield_curve"
                "&field_tdr_date_value={year}&page&_format=csv")
TREASURY_PAGE = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
                 "TextView?type=daily_treasury_yield_curve&field_tdr_date_value={year}")
FRED_SERIES = "BAMLH0A0HYM2"
FRED_API = "https://api.stlouisfed.org/fred/series/observations"
FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"
FRED_PAGE = "https://fred.stlouisfed.org/series/{series}"

SOURCE_UST = "U.S. Department of the Treasury — Daily Treasury Par Yield Curve Rates"
SOURCE_HY = "FRED, Federal Reserve Bank of St. Louis — ICE BofA US High Yield Index OAS (BAMLH0A0HYM2)"


class MarketDataError(Exception):
    pass


# ---------- кривая UST ----------

def tenor_years(label: str) -> float | None:
    """'1 Mo' -> 1/12, '1.5 Month' -> 0.125, '10 Yr' -> 10."""
    m = re.match(r"\s*([\d.]+)\s*(mo|month|yr|year)", str(label), re.I)
    if not m:
        return None
    x = float(m.group(1))
    return x / 12 if m.group(2).lower().startswith("mo") else x


def parse_treasury_csv(text: str) -> pd.DataFrame:
    """CSV Treasury -> таблица: индекс — дата, колонки — сроки в годах, значения — доходность в долях."""
    df = pd.read_csv(io.StringIO(text))
    df["Date"] = pd.to_datetime(df["Date"]).dt.date
    cols = {c: tenor_years(c) for c in df.columns if c != "Date"}
    cols = {c: t for c, t in cols.items() if t is not None}
    out = df.set_index("Date")[list(cols)].rename(columns=cols).sort_index(axis=1) / 100
    return out.sort_index()


def _cache_path(name):
    CACHE_DIR.mkdir(exist_ok=True)
    return CACHE_DIR / name


def _fetch(url, params=None, timeout=30, retries=3):
    last = None
    for i in range(retries):
        try:
            r = requests.get(url, params=params, headers=UA, timeout=timeout)
            r.raise_for_status()
            return r
        except requests.RequestException as e:   # повтор с паузой: 1, 2 сек
            last = e
            time.sleep(1 + i)
    raise MarketDataError(f"Источник недоступен: {type(last).__name__}")


def _with_cache(name, fetch_text, max_age_hours):
    """Текст из кэша, если он свежий; иначе — из источника (с сохранением в кэш); при сбое — старый кэш."""
    path, meta_path = _cache_path(name), _cache_path(name + ".json")
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    fetched_at = dt.datetime.fromisoformat(meta["fetched_at"]) if meta.get("fetched_at") else None
    fresh = fetched_at and (dt.datetime.now() - fetched_at).total_seconds() < max_age_hours * 3600
    if path.exists() and fresh:
        return path.read_text(encoding="utf-8"), fetched_at, "кэш"
    try:
        text = fetch_text()
        path.write_text(text, encoding="utf-8")
        now = dt.datetime.now()
        meta_path.write_text(json.dumps({"fetched_at": now.isoformat(timespec="seconds")}), encoding="utf-8")
        return text, now, "источник"
    except MarketDataError:
        if path.exists():
            return path.read_text(encoding="utf-8"), fetched_at, "кэш (источник недоступен)"
        raise


def treasury_year(year: int, max_age_hours=6) -> tuple[pd.DataFrame, dict]:
    today = dt.date.today()
    age = max_age_hours if year >= today.year else 24 * 365    # прошлые годы не меняются
    text, fetched, how = _with_cache(f"treasury_{year}.csv",
                                     lambda: _fetch(TREASURY_CSV.format(year=year)).text, age)
    return parse_treasury_csv(text), {"fetched_at": fetched, "how": how, "url": TREASURY_PAGE.format(year=year)}


@dataclass
class Curve:
    requested: dt.date
    date: dt.date                 # фактическая дата наблюдения (последний рабочий день ≤ requested)
    tenors: list                  # годы
    yields: list                  # доли
    source: str = SOURCE_UST
    url: str = ""
    fetched_at: dt.datetime | None = None
    how: str = ""

    def at(self, t: float) -> float:
        return interpolate(self.tenors, self.yields, t)

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame({"tenor": self.tenors, "yield": self.yields})


def interpolate(tenors, yields, t: float) -> float:
    """Линейная интерполяция доходности на срок t (лет). За пределами кривой — плоская экстраполяция
    (значение ближайшего срока): для t < 1 мес. и t > 30 лет."""
    x = np.array(tenors, dtype=float)
    y = np.array(yields, dtype=float)
    m = ~np.isnan(y)
    return float(np.interp(t, x[m], y[m]))


def ust_curve(on: dt.date) -> Curve:
    """Кривая UST на дату on или на последний рабочий день до неё."""
    frames, meta = [], None
    for year in (on.year, on.year - 1):
        try:
            df, meta_y = treasury_year(year)
        except MarketDataError:
            if year == on.year:
                raise
            continue
        meta = meta or meta_y
        frames.append(df)
        if (df.index <= on).any():
            break
    df = pd.concat(frames).sort_index()
    df = df[df.index <= on].dropna(how="all")
    if df.empty:
        raise MarketDataError(f"В данных Treasury нет кривой на {on:%d.%m.%Y} или раньше.")
    d = df.index[-1]
    row = df.loc[d]
    return Curve(requested=on, date=d, tenors=list(row.index), yields=list(row.values),
                 url=TREASURY_PAGE.format(year=d.year), fetched_at=meta["fetched_at"], how=meta["how"])


def curves_between(start: dt.date, end: dt.date) -> pd.DataFrame:
    """Все кривые Treasury за период: индекс — дата, колонки — сроки (лет), значения — доли."""
    parts = []
    for year in range(start.year, end.year + 1):
        try:
            parts.append(treasury_year(year)[0])
        except MarketDataError:
            continue
    if not parts:
        return pd.DataFrame()
    df = pd.concat(parts).sort_index()
    return df[(df.index >= start) & (df.index <= end)]


def ust_series(tenor: float, start: dt.date, end: dt.date) -> pd.Series:
    """Ряд доходности одного срока (например, 10Y) за период — из тех же годовых файлов Treasury."""
    parts = []
    for year in range(start.year, end.year + 1):
        try:
            df, _ = treasury_year(year)
        except MarketDataError:
            continue
        parts.append(df[tenor])
    if not parts:
        return pd.Series(dtype=float)
    s = pd.concat(parts).sort_index()
    return s[(s.index >= start) & (s.index <= end)].dropna()


# ---------- HY OAS (FRED) ----------

def parse_fred_json(js: dict) -> pd.Series:
    obs = pd.DataFrame(js["observations"])
    s = pd.to_numeric(obs["value"], errors="coerce")
    s.index = pd.to_datetime(obs["date"]).dt.date
    return s.dropna()


def parse_fred_csv(text: str) -> pd.Series:
    df = pd.read_csv(io.StringIO(text))
    s = pd.to_numeric(df.iloc[:, 1], errors="coerce")
    s.index = pd.to_datetime(df.iloc[:, 0]).dt.date
    return s.dropna()


def fred_series(series_id: str, api_key: str | None, start: dt.date | None = None,
                max_age_hours=6) -> tuple[pd.Series, dict]:
    """Ряд FRED «как есть» (для OAS-индексов ICE — в процентах). С кэшем и повторами."""
    start = start or dt.date.today() - dt.timedelta(days=3 * 365)

    def fetch():
        if api_key:
            r = _fetch(FRED_API, params={"series_id": series_id, "api_key": api_key, "file_type": "json",
                                         "observation_start": start.isoformat()})
            s = parse_fred_json(r.json())
        else:
            s = parse_fred_csv(_fetch(FRED_CSV.format(series=series_id), timeout=60).text)
        return s.to_frame("value").to_csv(index_label="date")

    text, fetched, how = _with_cache(f"fred_{series_id}.csv", fetch, max_age_hours)
    return parse_fred_csv(text), {"fetched_at": fetched, "how": how, "url": FRED_PAGE.format(series=series_id),
                                  "channel": "FRED API" if api_key else "FRED CSV (без ключа)"}


def hy_oas_series(api_key: str | None, start: dt.date | None = None, max_age_hours=6) -> tuple[pd.Series, dict]:
    """Ряд HY OAS в базисных пунктах. FRED публикует значения в процентах (3,40 = 340 бп)."""
    s, meta = fred_series(FRED_SERIES, api_key, start, max_age_hours)
    return s * 100, meta


def value_on(series: pd.Series, on: dt.date):
    """(дата наблюдения, значение) — последнее наблюдение не позже даты on."""
    s = series[series.index <= on]
    if s.empty:
        return None, None
    return s.index[-1], float(s.iloc[-1])


# ---------- сборка рыночного фона для портфеля ----------

@dataclass
class Market:
    as_of: dt.date | None
    curve_asof: Curve | None = None
    curve_latest: Curve | None = None
    hy_series: pd.Series | None = None
    hy_asof: tuple | None = None          # (дата, бп)
    hy_latest: tuple | None = None
    ust10_series: pd.Series | None = None
    hy_meta: dict = field(default_factory=dict)
    errors: list = field(default_factory=list)

    @property
    def ok_curve(self):
        return self.curve_asof is not None

    def ust10(self, which="asof"):
        c = self.curve_asof if which == "asof" else self.curve_latest
        return None if c is None else c.at(10)


def load_market(as_of: dt.date | None, fred_key: str | None, today: dt.date | None = None,
                history_days: int = 365) -> Market:
    today = today or dt.date.today()
    m = Market(as_of=as_of)
    ref = as_of or today
    try:
        m.curve_asof = ust_curve(ref)
    except MarketDataError as e:
        m.errors.append(f"Кривая UST на дату портфеля: {e}")
    try:
        m.curve_latest = ust_curve(today)
    except MarketDataError as e:
        m.errors.append(f"Последняя кривая UST: {e}")
    try:
        m.ust10_series = ust_series(10.0, ref - dt.timedelta(days=history_days), today)
    except MarketDataError as e:
        m.errors.append(f"Ряд 10Y UST: {e}")
    try:
        m.hy_series, m.hy_meta = hy_oas_series(fred_key, start=ref - dt.timedelta(days=max(history_days, 400)))
        m.hy_asof = value_on(m.hy_series, ref)
        m.hy_latest = value_on(m.hy_series, today)
    except (MarketDataError, KeyError, ValueError) as e:
        m.errors.append(f"HY OAS (FRED): {e}")
    return m


# ---------- сверка рыночных цифр, упомянутых в файле ----------

def check_file_claims(texts: list[str], market: Market, tol_bp: float = 1.0) -> list[dict]:
    """Ищет в текстах дашборда упоминания «10Y UST 4,94%» и сверяет с Treasury на дату портфеля."""
    out = []
    if market.curve_asof is None:
        return out
    official = market.ust10("asof")
    for text in texts:
        # Одно число сразу перед «%»: «10Y UST 4,94%». Диапазоны лестницы («10Y 5,10–5,15%») не совпадают.
        for m in re.finditer(r"10\s*-?\s*Y(?:\s*UST)?\s*(\d+[.,]\d+)\s*%", text, re.I):
            stated = float(m.group(1).replace(",", ".")) / 100
            # Контекст — предложение, в котором встретилось число (без обрезки слов).
            dot, nl = text.rfind(". ", 0, m.start()), text.rfind("\n", 0, m.start())
            s0 = max(dot + 2 if dot != -1 else 0, nl + 1 if nl != -1 else 0)
            ends = [i for i in (text.find(". ", m.end()), text.find("\n", m.end())) if i != -1]
            ctx = text[s0: min(ends) + 1 if ends else len(text)].replace("\n", " ")
            if len(ctx) > 220:
                ctx = ctx[:220].rsplit(" ", 1)[0] + "…"
            diff = (stated - official) * 10000
            out.append({"stated": stated, "official": official, "diff_bp": diff, "date": market.curve_asof.date,
                        "context": ctx.strip(), "ok": abs(diff) <= tol_bp})
    return out
