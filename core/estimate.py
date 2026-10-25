"""Оценка портфеля на последнюю рыночную дату по официальным индексам (между файлами компании).

Для каждой позиции: ΔYTM ≈ ΔUST(T) + ΔOAS индекса; оценка YTM = YTM из файла + ΔYTM;
оценка G-spread = G-spread из файла + ΔOAS; ценовой эффект ≈ −MD × ΔYTM; купонный доход (carry) ≈
YTM × дней / 365. Это ОЦЕНКА: предполагается, что спред бумаги меняется так же, как спред её индекса.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import ratings
from .bondmath import years_between
from .loader import Snapshot
from .market import FRED_PAGE, Curve, MarketDataError, fred_series, ust_curve, value_on

INDEX_NAMES = {
    "AAA": "ICE BofA AAA US Corporate", "AA": "ICE BofA AA US Corporate", "A": "ICE BofA Single-A US Corporate",
    "BBB": "ICE BofA BBB US Corporate", "BB": "ICE BofA BB US High Yield", "B": "ICE BofA Single-B US High Yield",
    "CCC": "ICE BofA CCC & Lower US High Yield", "EM_IG": "ICE BofA High Grade EM Corporate Plus",
    "EM_HY": "ICE BofA High Yield EM Corporate Plus", "HY_FUND": "ICE BofA US High Yield",
}


def index_bucket(row) -> str | None:
    """Ключ индекса для позиции: по региону и рейтингу. None — кэш (только UST)."""
    if row["instrument_type"] == "cash":
        return None
    ig = ratings.is_ig(row["rating"])
    if row["instrument_type"] == "fund":
        return "HY_FUND" if ig is False else "BBB"
    if str(row.get("region") or "").upper() == "EM":
        return "EM_HY" if ig is False else "EM_IG"
    letter = ratings.letter(row["rating"])
    if letter in ("CC", "C", "D"):
        letter = "CCC"
    return letter


@dataclass
class Estimate:
    as_of: dt.date
    est_date: dt.date
    positions: pd.DataFrame
    indices: pd.DataFrame
    portfolio: dict
    notes: list = field(default_factory=list)


def _term(as_of, row):
    mat = row["maturity"]
    if row["instrument_type"] == "bond" and mat is not None and not pd.isna(mat) and as_of and mat > as_of:
        return years_between(as_of, mat)
    return row["mod_duration"] if pd.notna(row["mod_duration"]) else None


def estimate(snap: Snapshot, cfg: dict, fred_key: str | None, curve_asof: Curve | None = None,
             curve_latest: Curve | None = None, today: dt.date | None = None,
             series_loader=None) -> Estimate | None:
    """series_loader(series_id) -> (pd.Series в %, meta) — подменяется в тестах."""
    today = today or dt.date.today()
    if snap.as_of is None:
        return None
    loader = series_loader or (lambda sid: fred_series(sid, fred_key, snap.as_of - dt.timedelta(days=30)))
    curve_asof = curve_asof or ust_curve(snap.as_of)
    curve_latest = curve_latest or ust_curve(today)
    p = snap.positions.copy()
    p["bucket"] = p.apply(index_bucket, axis=1)
    notes, idx_rows, loaded = [], [], {}
    for b in sorted({x for x in p["bucket"] if isinstance(x, str)}):
        sid = cfg["estimate"]["series"].get(b)
        try:
            s, meta = loader(sid)
            loaded[b] = (s * 100, meta, sid)            # проценты -> бп
        except (MarketDataError, KeyError, ValueError) as e:
            notes.append(f"Индекс {INDEX_NAMES.get(b, b)} ({sid}) недоступен ({e}). По бумагам, привязанным к нему, "
                         "учтено только изменение ставок UST, поэтому их оценка неполная.")
    # Дата оценки: последняя дата, на которую есть и кривая, и все индексы.
    last_dates = [curve_latest.date] + [s.index.max() for s, _, _ in loaded.values()]
    est_date = min(last_dates)
    curve_est = curve_latest if curve_latest.date == est_date else ust_curve(est_date)
    for b, (s, meta, sid) in loaded.items():
        d0, v0 = value_on(s, snap.as_of)
        d1, v1 = value_on(s, est_date)
        idx_rows.append({"Группа": b, "Индекс": INDEX_NAMES.get(b, b), "Серия FRED": sid, "Ссылка": FRED_PAGE.format(series=sid),
                         "Дата портфеля": d0, "OAS на дату портфеля, бп": v0, "Дата оценки": d1, "OAS на дату оценки, бп": v1,
                         "Δ OAS, бп": (v1 - v0) if v0 is not None and v1 is not None else np.nan,
                         "Позиций": int((p["bucket"] == b).sum())})
    indices = pd.DataFrame(idx_rows)
    d_oas = dict(zip(indices["Группа"], indices["Δ OAS, бп"])) if not indices.empty else {}
    days = (est_date - snap.as_of).days

    rows = []
    for _, r in p.iterrows():
        t0, t1 = _term(snap.as_of, r), _term(est_date, r)
        dust = (curve_est.at(t1) - curve_asof.at(t0)) * 1e4 if t0 is not None and t1 is not None else np.nan
        has_idx = isinstance(r["bucket"], str)
        doas = d_oas.get(r["bucket"], 0.0) if has_idx else 0.0
        doas = 0.0 if pd.isna(doas) else doas
        dy = dust + doas
        price = -r["mod_duration"] * dy / 1e4 if pd.notna(r["mod_duration"]) and pd.notna(dy) else np.nan
        carry = r["ytm"] * days / 365 if pd.notna(r["ytm"]) else np.nan
        rows.append({"Позиция": r["label"], "Вес": r["weight"], "Индекс": INDEX_NAMES.get(r["bucket"], "— (только UST)")
                     if has_idx else "— (только UST)", "YTM в файле": r["ytm"], "Δ UST, бп": dust, "Δ OAS индекса, бп": doas,
                     "Δ YTM (оценка), бп": dy, "YTM (оценка)": r["ytm"] + dy / 1e4 if pd.notna(dy) else np.nan,
                     "G-spread в файле": r["gspread"],
                     "G-spread (оценка)": r["gspread"] + doas if pd.notna(r["gspread"]) else np.nan,
                     "Mod. duration": r["mod_duration"], "Ценовой эффект": price, "Купонный доход": carry,
                     "Итого доходность": price + carry if pd.notna(price) and pd.notna(carry) else np.nan})
    pos = pd.DataFrame(rows)
    w = pos["Вес"].fillna(0)

    def ws(col):
        return float((w * pos[col].fillna(0)).sum())

    portfolio = {"ytm_file": ws("YTM в файле"), "ytm_est": ws("YTM (оценка)"), "gs_file": ws("G-spread в файле"),
                 "gs_est": ws("G-spread (оценка)"), "d_ust_bp": ws("Δ UST, бп"), "d_oas_bp": ws("Δ OAS индекса, бп"),
                 "price": ws("Ценовой эффект"), "carry": ws("Купонный доход"), "total": ws("Итого доходность"),
                 "days": days}
    notes.append("Это оценка по индексам, а не котировки бумаг: предполагается, что спред бумаги меняется так "
                 "же, как OAS её индекса ICE BofA. OAS и G-spread — разные меры, поэтому используются только "
                 "изменения OAS, а не его уровень. Выпуклость не учитывается, поэтому при больших сдвигах ставок "
                 "оценка цены менее точна.")
    return Estimate(as_of=snap.as_of, est_date=est_date, positions=pos, indices=indices, portfolio=portfolio, notes=notes)
