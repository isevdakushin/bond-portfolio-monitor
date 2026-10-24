"""Сигналы по спредам.

1. Один снимок — «шире группы»: G-spread бумаги заметно выше медианы пиров (та же рейтинговая группа,
   похожая дюрация). Рынок закладывает в бумагу риск сверх её рейтинга. Симметрично — «уже группы».
2. Два снимка — «аномальное расширение»: спред бумаги вырос сильнее, чем медианно вырос спред
   её рейтинговой группы за тот же период.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import ratings
from .compare import match_positions
from .loader import Snapshot


def _bonds(p: pd.DataFrame, spread: pd.Series | None = None) -> pd.DataFrame:
    b = p[(p["instrument_type"] == "bond")].copy()
    b["spread"] = spread.loc[b.index] if spread is not None else b["gspread"]
    b["group"] = b["rating"].map(ratings.group)
    return b[b["spread"].notna() & b["mod_duration"].notna() & b["group"].notna()]


def duration_slope(b: pd.DataFrame) -> float:
    """Наклон «спред на год дюрации», бп/год: МНК внутри рейтинговых групп (от средних по группе),
    общий для всех групп. Не меньше 0."""
    x = b["mod_duration"] - b.groupby("group")["mod_duration"].transform("mean")
    y = b["spread"] - b.groupby("group")["spread"].transform("mean")
    den = float((x ** 2).sum())
    return max(0.0, float((x * y).sum()) / den) if den else 0.0


def relative_value(snap: Snapshot, cfg: dict, spread: pd.Series | None = None) -> pd.DataFrame:
    """Премия спреда бумаги к медиане пиров, приведённой к дюрации бумаги.
    spread — альтернативный ряд спредов (например, пересчитанный)."""
    s = cfg["signals"]
    b = _bonds(snap.positions, spread)
    slope = duration_slope(b) if s.get("duration_adjust", True) else 0.0
    rows = []
    for idx, r in b.iterrows():
        same = b[(b["group"] == r["group"]) & (b.index != idx)]
        window, peers = s["duration_window"], pd.DataFrame()
        max_window = float(same["mod_duration"].sub(r["mod_duration"]).abs().max()) if len(same) else 0
        while True:
            peers = same[(same["mod_duration"] - r["mod_duration"]).abs() <= window]
            if len(peers) >= s["min_peers"] or window >= max_window:
                break
            window += 1.0
        if peers.empty:
            rows.append({"Позиция": r["label"], "Рейтинг": r["rating"], "Группа": r["group"], "Регион": r["region"],
                         "Mod. duration": r["mod_duration"], "G-spread": r["spread"], "Медиана пиров": None,
                         "Пиров": 0, "Окно дюрации, ±лет": None, "Премия, бп": None, "Премия, %": None,
                         "Сигнал": "нет пиров", "Пиры": ""})
            continue
        # Спред пира, приведённый к дюрации бумаги: s_peer + наклон × (MD бумаги − MD пира).
        adj = peers["spread"] + slope * (r["mod_duration"] - peers["mod_duration"])
        med = float(adj.median())
        prem = r["spread"] - med
        rel = prem / med if med else np.nan
        if prem > s["wide_abs_bp"] and rel > s["wide_rel"]:
            sig = "шире группы"
        elif prem < -s["wide_abs_bp"] and rel < -s["wide_rel"]:
            sig = "уже группы"
        else:
            sig = "в норме"
        note = []
        if len(peers) < s["min_peers"]:
            note.append(f"мало пиров ({len(peers)})")
        if window > s["duration_window"]:
            note.append(f"окно дюрации расширено до ±{window:g} лет")
        rows.append({"Позиция": r["label"], "Рейтинг": r["rating"], "Группа": r["group"], "Регион": r["region"],
                     "Mod. duration": r["mod_duration"], "G-spread": r["spread"], "Медиана пиров": med,
                     "Пиров": len(peers), "Окно дюрации, ±лет": window, "Премия, бп": prem, "Премия, %": rel,
                     "Сигнал": sig, "Надёжность": "; ".join(note) or "достаточно пиров",
                     "Пиры": ", ".join(f"{n} ({v:.0f} бп, MD {d:.1f})" for n, v, d in
                                       zip(peers["issuer_short"], peers["spread"], peers["mod_duration"]))})
    out = pd.DataFrame(rows)
    out.attrs["slope"] = slope
    order = {"шире группы": 0, "уже группы": 1, "в норме": 2, "нет пиров": 3}
    if not out.empty:
        out = out.assign(_o=out["Сигнал"].map(order)).sort_values(["_o", "Премия, бп"], ascending=[True, False]) \
            .drop(columns="_o").reset_index(drop=True)
        out.attrs["slope"] = slope
    return out


def spread_changes(cur: Snapshot, prev: Snapshot, cfg: dict) -> pd.DataFrame:
    """Изменение G-spread по бумагам, оставшимся в портфеле, и сравнение с медианным изменением группы."""
    s = cfg["signals"]
    m = match_positions(cur.positions, prev.positions)
    m = m[(m["Статус"] == "осталась") & (m["instrument_type"] == "bond")].copy()
    m = m[m["gspread"].notna() & m["gspread_prev"].notna()]
    if m.empty:
        return pd.DataFrame()
    m["group"] = m["rating"].map(ratings.group)
    m["d_spread"] = m["gspread"] - m["gspread_prev"]
    m["group_med"] = m.groupby("group")["d_spread"].transform("median")
    m["n_group"] = m.groupby("group")["d_spread"].transform("size")
    all_med = float(m["d_spread"].median())
    # В группе из одной бумаги сравнивать не с чем — берём медиану по всем бумагам.
    m["base"] = np.where(m["n_group"] > 1, m["group_med"], all_med)
    m["excess"] = m["d_spread"] - m["base"]
    m["Сигнал"] = np.where(m["excess"] > s["widening_abs_bp"], "аномальное расширение",
                           np.where(m["excess"] < -s["widening_abs_bp"], "аномальное сужение", "в норме"))
    m["d_rating"] = [(ratings.score(a) or np.nan) - (ratings.score(b) or np.nan)
                     for a, b in zip(m["rating"], m["rating_prev"])]
    out = pd.DataFrame({
        "Позиция": m["label"], "Рейтинг": m["rating"], "Рейтинг ранее": m["rating_prev"], "Группа": m["group"],
        "G-spread ранее": m["gspread_prev"], "G-spread сейчас": m["gspread"], "Изменение, бп": m["d_spread"],
        "Медианное изменение группы, бп": m["base"], "Сверх группы, бп": m["excess"],
        "Изменение рейтинга, ступеней": m["d_rating"], "Сопоставлено по": m["match_by"], "Сигнал": m["Сигнал"]})
    return out.sort_values("Сверх группы, бп", ascending=False).reset_index(drop=True)
