"""Независимый пересчёт G-spread и сверка с файлом.

G-spread = YTM бумаги − доходность UST, линейно интерполированная по par-кривой Treasury
на дату снимка. Два способа выбора срока для интерполяции:
- по сроку до погашения (основной, рыночный стандарт);
- по модифицированной дюрации (для сравнения).
Пересчёт делается только для облигаций: фонды (ETF без даты погашения) и денежные позиции помечаются.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .bondmath import years_between
from .loader import Snapshot
from .market import Curve, interpolate

METHOD_LABEL = {"maturity": "по сроку до погашения", "duration": "по дюрации"}


@dataclass
class GSpreadResult:
    table: pd.DataFrame            # по позициям
    summary: pd.DataFrame          # сравнение способов
    primary: str
    curve: Curve
    systematic: dict               # {"median": бп, "flag": bool}
    implied: dict = field(default_factory=dict)   # диагностика: дата кривой, лучше всего объясняющая файл
    portfolio: dict = field(default_factory=dict)  # средневзвешенный G-spread: файл и пересчёт


def recompute(snap: Snapshot, curve: Curve, cfg: dict, curves_history: pd.DataFrame | None = None) -> GSpreadResult:
    g = cfg["gspread"]
    primary = g["method_primary"]
    rows = []
    for _, r in snap.positions.iterrows():
        rec = {"Позиция": r["label"], "Тип": r["instrument_type"], "Вес": r["weight"], "Рейтинг": r["rating"],
               "YTM": r["ytm"], "G-spread в файле": r["gspread"], "Срок, лет": None, "Mod. duration": r["mod_duration"],
               "UST на срок": None, "G (срок)": None, "UST на дюрацию": None, "G (дюрация)": None, "Комментарий": ""}
        if r["instrument_type"] != "bond":
            rec["Комментарий"] = ("ETF без даты погашения, не пересчитывается" if r["instrument_type"] == "fund"
                                  else "денежная позиция, не пересчитывается")
            rows.append(rec)
            continue
        if pd.isna(r["ytm"]):
            rec["Комментарий"] = "нет YTM, не пересчитывается"
            rows.append(rec)
            continue
        mat = r["maturity"]
        if mat is not None and not pd.isna(mat) and mat > curve.date:
            t = years_between(snap.as_of or curve.date, mat)
            rec["Срок, лет"] = t
            rec["UST на срок"] = curve.at(t)
            rec["G (срок)"] = (r["ytm"] - rec["UST на срок"]) * 1e4
        else:
            rec["Комментарий"] = "нет даты погашения — только по дюрации"
        if pd.notna(r["mod_duration"]):
            rec["UST на дюрацию"] = curve.at(r["mod_duration"])
            rec["G (дюрация)"] = (r["ytm"] - rec["UST на дюрацию"]) * 1e4
        nm = r.get("name_maturity")
        if nm is not None and not pd.isna(nm) and mat is not None and not pd.isna(mat) and nm != mat:
            g_alt = (r["ytm"] - curve.at(years_between(snap.as_of or curve.date, nm))) * 1e4
            rec["Комментарий"] = (f"погашение в названии ({nm:%d.%m.%Y}) ≠ колонке ({mat:%d.%m.%Y}); "
                                  f"по дате из названия G = {g_alt:.0f} бп")
            rec["G (срок, дата из названия)"] = g_alt
        rows.append(rec)
    t = pd.DataFrame(rows)
    for m, col in (("maturity", "G (срок)"), ("duration", "G (дюрация)")):
        t[f"Δ {METHOD_LABEL[m]}"] = t[col] - t["G-spread в файле"]

    pcol = f"Δ {METHOD_LABEL[primary]}"
    diffs = t[pcol].dropna()
    med = float(diffs.median()) if len(diffs) else None
    systematic = {"median": med, "flag": med is not None and abs(med) > g["systematic_bp"],
                  "threshold": g["systematic_bp"]}
    # Выбросы — относительно общего (медианного) расхождения, чтобы системный сдвиг не делал выбросами всех.
    t["Отклонение от общего, бп"] = t[pcol] - (med or 0)
    t["Статус"] = np.where(t[pcol].isna(), "—",
                           np.where(t["Отклонение от общего, бп"].abs() > g["outlier_bp"], "выброс", "в норме"))

    summary = []
    for m in ("maturity", "duration"):
        d = t[f"Δ {METHOD_LABEL[m]}"].dropna()
        summary.append({"Способ": METHOD_LABEL[m] + (" (основной)" if m == primary else ""), "Бумаг": len(d),
                        "Среднее расхождение, бп": d.mean(), "Медиана, бп": d.median(),
                        "Средний модуль, бп": d.abs().mean(), "Разброс (σ), бп": d.std(ddof=0),
                        f"В пределах ±{g['outlier_bp']} бп": int((d.abs() <= g["outlier_bp"]).sum())})
    summary = pd.DataFrame(summary)

    w = t["Вес"]
    calc_primary = t["G (срок)" if primary == "maturity" else "G (дюрация)"].fillna(t["G-spread в файле"])
    portfolio = {"file": float((w * t["G-spread в файле"]).sum()), "recomputed": float((w * calc_primary).sum())}

    implied = {}
    if curves_history is not None and snap.as_of:
        implied = implied_curve_date(snap, curves_history, g["implied_date_window_days"])
    return GSpreadResult(table=t, summary=summary, primary=primary, curve=curve, systematic=systematic,
                         implied=implied, portfolio=portfolio)


def implied_curve_date(snap: Snapshot, curves: pd.DataFrame, window_days: int) -> dict:
    """Для каждой даты кривой в окне до даты снимка и каждого способа — средний модуль расхождения
    с G-spread из файла. Возвращает лучшую дату по каждому способу. Это диагностика, а не факт."""
    b = snap.positions[(snap.positions["instrument_type"] == "bond") & snap.positions["ytm"].notna()
                       & snap.positions["gspread"].notna()]
    if b.empty:
        return {}
    start = snap.as_of - dt.timedelta(days=window_days)
    cv = curves[(curves.index <= snap.as_of) & (curves.index >= start)]
    t_mat = [years_between(snap.as_of, m) if m is not None and not pd.isna(m) else np.nan for m in b["maturity"]]
    out = {}
    for method, terms in (("maturity", t_mat), ("duration", list(b["mod_duration"]))):
        best = None
        for d, row in cv.iterrows():
            ten, ys = list(row.index), list(row.values)
            diff = [(y - interpolate(ten, ys, tt)) * 1e4 - gs for y, tt, gs in zip(b["ytm"], terms, b["gspread"])
                    if not pd.isna(tt)]
            if not diff:
                continue
            mae = float(np.mean(np.abs(diff)))
            if best is None or mae < best["mae"]:
                best = {"date": d, "mae": mae, "bias": float(np.mean(diff)), "sd": float(np.std(diff)),
                        "ust10": float(row.get(10.0, np.nan))}
        if best:
            out[method] = best
    out["window"] = (start, snap.as_of)
    return out
