"""Стресс-тест: мгновенные шоки ставок UST и кредитных спредов. P&L позиции ≈ −MD × Δy."""
from __future__ import annotations

import pandas as pd

from . import ratings
from .loader import Snapshot


def position_shock(row, sc: dict) -> tuple[float, float]:
    """(Δ UST, Δ спреда) в бп для позиции в сценарии."""
    ust = float(sc.get("ust", 0))
    if row["instrument_type"] == "cash":
        return ust, 0.0
    em = str(row.get("region") or "").upper() == "EM"
    ig = ratings.is_ig(row["rating"])
    hy = ig is False
    cands = []
    if em:
        cands.append(float(sc.get("em", 0)))
    if hy:
        cands.append(float(sc.get("hy", 0)))
    if not em and not hy:
        cands.append(float(sc.get("ig", 0)))
    return ust, max(cands) if cands else 0.0


def run_scenario(snap: Snapshot, sc: dict) -> pd.DataFrame:
    rows = []
    for _, r in snap.positions.iterrows():
        dust, dspr = position_shock(r, sc)
        dy = dust + dspr
        pnl = -r["mod_duration"] * dy / 1e4 if pd.notna(r["mod_duration"]) else 0.0
        rows.append({"Позиция": r["label"], "Sleeve": r["sleeve"], "Вес": r["weight"], "Mod. duration": r["mod_duration"],
                     "Δ UST, бп": dust, "Δ спреда, бп": dspr, "P&L позиции": pnl, "P&L, % NAV": r["weight"] * pnl})
    return pd.DataFrame(rows)


def run_all(snap: Snapshot, cfg: dict, scenarios: list | None = None) -> dict:
    scenarios = scenarios or cfg["stress"]["scenarios"]
    summary, details = [], {}
    for sc in scenarios:
        d = run_scenario(snap, sc)
        details[sc["name"]] = d
        by_sleeve = d.groupby("Sleeve")["P&L, % NAV"].sum()
        worst = d.nsmallest(1, "P&L, % NAV").iloc[0]
        summary.append({"Сценарий": sc["name"], "UST, бп": sc.get("ust", 0), "EM, бп": sc.get("em", 0),
                        "HY, бп": sc.get("hy", 0), "IG (не EM), бп": sc.get("ig", 0),
                        "P&L портфеля, % NAV": float(d["P&L, % NAV"].sum()),
                        "Наибольший вклад (sleeve)": by_sleeve.idxmin() if by_sleeve.min() < 0 else by_sleeve.idxmax(),
                        "Худшая позиция": worst["Позиция"], "P&L худшей позиции, % NAV": float(worst["P&L, % NAV"])})
    return {"summary": pd.DataFrame(summary), "details": details}
