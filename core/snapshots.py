"""Сравнение двух снимков портфеля.

- метрики портфеля и аллокация: было → стало;
- позиции: сопоставление по ISIN / названию / эмитенту и погашению; новые, выбывшие, оставшиеся;
  изменения веса, YTM, G-spread, дюрации, рейтинга;
- разложение изменения средневзвешенной метрики (YTM, G-spread, дюрация) на вклады:
  изменение значений по оставшимся бумагам (для YTM — отдельно рынок UST и спред), перевзвешивание,
  новые и выбывшие бумаги. Сумма вкладов равна изменению метрики портфеля.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import ratings
from .analytics import analyze
from .bondmath import years_between
from .compare import match_positions
from .loader import Snapshot


@dataclass
class Comparison:
    cur_date: object
    prev_date: object
    metrics: pd.DataFrame
    attribution: dict                 # метрика -> DataFrame вкладов
    allocation: pd.DataFrame | None
    positions: pd.DataFrame
    rating_changes: pd.DataFrame
    concentration_changes: pd.DataFrame
    notes: list = field(default_factory=list)


def order_snapshots(a: Snapshot, b: Snapshot):
    """(текущий, предыдущий, примечание): если даты перепутаны — меняет местами."""
    if a.as_of and b.as_of and b.as_of > a.as_of:
        return b, a, (f"Файлы перепутаны: «{a.source_name}» ({a.as_of:%d.%m.%Y}) старше "
                      f"«{b.source_name}» ({b.as_of:%d.%m.%Y}). Текущим взят более поздний.")
    if a.as_of and b.as_of and a.as_of == b.as_of:
        return a, b, f"У снимков одна дата ({a.as_of:%d.%m.%Y}) — проверить, что загружены разные файлы."
    return a, b, None


def inherit_targets(cur: Snapshot, prev: Snapshot) -> tuple[Snapshot, list]:
    """Если в новом файле нет целей, аллокации или текста правил — берутся из предыдущего снимка (с пометкой)."""
    out, notes = copy.copy(cur), []
    when = f"от {prev.as_of:%d.%m.%Y}" if prev.as_of else "предыдущего снимка"
    if not cur.dashboard_metrics and prev.dashboard_metrics:
        out.dashboard_metrics = copy.deepcopy(prev.dashboard_metrics)
        for m in out.dashboard_metrics.values():
            m["current"] = None                     # текущие значения старого дашборда не переносим
        notes.append(f"Целевые метрики взяты из снимка {when}: в новом файле их нет.")
    if (cur.allocation is None or cur.allocation.empty) and prev.allocation is not None and not prev.allocation.empty:
        from .loader import map_sleeves
        a = prev.allocation[["sleeve", "target"]].copy()
        a["current"], a["gap"] = None, None
        mp = map_sleeves(a["sleeve"], cur.positions["sleeve"])
        a["portfolio_sleeve"] = a["sleeve"].map(lambda s: mp[s][0])
        a["match"] = a["sleeve"].map(lambda s: mp[s][1])
        out.allocation = a
        notes.append(f"Целевая аллокация взята из снимка {when}: в новом файле её нет.")
    if not cur.dashboard_texts and not any(cur.sheet_texts.values()) and (prev.dashboard_texts or prev.sheet_texts):
        out.dashboard_texts = list(prev.dashboard_texts)
        out.sheet_texts = dict(prev.sheet_texts)
        notes.append(f"Правила добора взяты из снимка {when}.")
    return out, notes


def _ust_at(curve, as_of, maturity, md, typ):
    if curve is None:
        return np.nan
    if typ == "bond" and maturity is not None and not pd.isna(maturity) and as_of:
        return curve.at(years_between(as_of, maturity))
    return curve.at(md) if md is not None and not pd.isna(md) else np.nan


def _decompose(m: pd.DataFrame, col: str, with_market: bool = False) -> pd.DataFrame:
    """Разложение Δ(Σ w·x) на вклады. m — результат match_positions."""
    cont = m[m["Статус"] == "осталась"]
    new = m[m["Статус"] == "новая"]
    rem = m[m["Статус"] == "выбыла"]
    w0, w1 = cont["weight_prev"].fillna(0), cont["weight"].fillna(0)
    x0, x1 = cont[f"{col}_prev"], cont[col]
    ok = x0.notna() & x1.notna()
    value_change = float((w0[ok] * (x1[ok] - x0[ok])).sum())
    reweight = float(((w1 - w0)[ok] * x1[ok]).sum())
    # Бумаги, у которых значение есть только в одном из снимков, — как вход/выход.
    only_new = float((w1[~ok & x1.notna()] * x1[~ok & x1.notna()]).sum())
    only_old = float((w0[~ok & x0.notna()] * x0[~ok & x0.notna()]).sum())
    added = float((new["weight"].fillna(0) * new[col]).sum(skipna=True)) + only_new
    removed = -float((rem["weight_prev"].fillna(0) * rem[f"{col}_prev"]).sum(skipna=True)) - only_old
    rows = []
    if with_market:
        du = (cont["ust"] - cont["ust_prev"])[ok]
        market = float((w0[ok] * du).sum(skipna=True))
        rows += [("Рынок UST (сдвиг кривой на сроки бумаг)", market),
                 ("Спред и прочее по оставшимся бумагам", value_change - market)]
    else:
        rows.append(("Изменение значений по оставшимся бумагам", value_change))
    rows += [("Перевзвешивание оставшихся бумаг", reweight), ("Новые бумаги", added), ("Выбывшие бумаги", removed)]
    df = pd.DataFrame(rows, columns=["Компонент", "Вклад"])
    return df


def compare(cur: Snapshot, prev: Snapshot, cfg: dict, curve_cur=None, curve_prev=None) -> Comparison:
    c_cfg = cfg["compare"]
    notes = []
    a1, a0 = analyze(cur, cfg), analyze(prev, cfg)
    m1, m0 = a1.metrics, a0.metrics

    defs = [("ytm_gross", "YTM брутто", "pct"), ("ytm_net", "YTM после комиссии", "pct"),
            ("mod_duration", "Mod. duration, лет", "num"), ("gspread", "G-spread, бп", "bp"),
            ("dts", "DTS (дюрация × спред)", "num"), ("avg_rating", "Средний рейтинг", "txt"),
            ("ig_share", "Доля IG", "share"), ("hy_share", "Доля HY", "share"), ("cash_share", "Доля кэша", "share"),
            ("em_share", "Доля EM", "share"), ("n_positions", "Позиций", "int"), ("n_issuers", "Эмитентов", "int")]
    rows = []
    for k, label, kind in defs:
        v0, v1 = m0.get(k), m1.get(k)
        if kind == "txt":
            d = (m1["avg_rating_score"] - m0["avg_rating_score"]) if m1.get("avg_rating_score") and m0.get("avg_rating_score") else None
            rows.append({"key": k, "Метрика": label, "Ранее": v0, "Сейчас": v1, "Изменение": d, "Тип": kind})
        else:
            rows.append({"key": k, "Метрика": label, "Ранее": v0, "Сейчас": v1,
                         "Изменение": None if v0 is None or v1 is None else v1 - v0, "Тип": kind})
    t1 = a1.targets.set_index("key")
    for k in ("ytm_gross", "mod_duration", "gspread"):
        tgt = t1.loc[k, "Цель"] if k in t1.index else None
        for r in rows:
            if r["key"] == k:
                r["Цель (сейчас)"] = tgt
    metrics = pd.DataFrame(rows)

    # Позиции
    m = match_positions(cur.positions, prev.positions)
    m["ust"] = [_ust_at(curve_cur, cur.as_of, mt, md, tp) for mt, md, tp in
                zip(m.get("maturity"), m.get("mod_duration"), m.get("instrument_type"))]
    m["ust_prev"] = [_ust_at(curve_prev, prev.as_of, mt, md, tp) for mt, md, tp in
                     zip(m.get("maturity_prev"), m.get("mod_duration_prev"), m.get("instrument_type_prev"))]
    have_curves = curve_cur is not None and curve_prev is not None
    attribution = {
        "YTM, бп": _decompose(m, "ytm", with_market=have_curves),
        "G-spread, бп": _decompose(m, "gspread"),
        "Mod. duration, лет": _decompose(m, "mod_duration"),
    }
    attribution["YTM, бп"]["Вклад"] *= 10000
    for k, d in attribution.items():
        total = d["Вклад"].sum()
        d.loc[len(d)] = ["Итого изменение портфеля", total]
    if not have_curves:
        notes.append("Нет кривой UST на одну из дат — изменение YTM не разложено на рынок и спред.")

    pos = pd.DataFrame({
        "Позиция": [l if isinstance(l, str) else lp for l, lp in zip(m.get("label"), m.get("label_prev"))],
        "Статус": m["Статус"], "Сопоставлено по": m["match_by"],
        "Sleeve": [s if isinstance(s, str) else sp for s, sp in zip(m.get("sleeve"), m.get("sleeve_prev"))],
        "Вес ранее": m.get("weight_prev"), "Вес сейчас": m.get("weight"),
        "YTM ранее": m.get("ytm_prev"), "YTM сейчас": m.get("ytm"),
        "G-spread ранее": m.get("gspread_prev"), "G-spread сейчас": m.get("gspread"),
        "MD ранее": m.get("mod_duration_prev"), "MD сейчас": m.get("mod_duration"),
        "Рейтинг ранее": m.get("rating_prev"), "Рейтинг сейчас": m.get("rating"),
    })
    pos["Δ вес, п.п."] = (pos["Вес сейчас"].fillna(0) - pos["Вес ранее"].fillna(0)) * 100
    pos["Δ YTM, бп"] = (pos["YTM сейчас"] - pos["YTM ранее"]) * 10000
    pos["Δ G-spread, бп"] = pos["G-spread сейчас"] - pos["G-spread ранее"]
    pos["Δ MD"] = pos["MD сейчас"] - pos["MD ранее"]
    pos["Δ рейтинга, ступеней"] = [(ratings.score(a) or np.nan) - (ratings.score(b) or np.nan)
                                   for a, b in zip(pos["Рейтинг сейчас"], pos["Рейтинг ранее"])]
    flags = []
    for _, r in pos.iterrows():
        f = []
        if r["Статус"] != "осталась":
            f.append(r["Статус"])
        if pd.notna(r["Δ рейтинга, ступеней"]) and r["Δ рейтинга, ступеней"] > 0:
            f.append(f"понижение рейтинга на {int(r['Δ рейтинга, ступеней'])} ст.")
        if pd.notna(r["Δ рейтинга, ступеней"]) and r["Δ рейтинга, ступеней"] < 0:
            f.append(f"повышение рейтинга на {int(-r['Δ рейтинга, ступеней'])} ст.")
        if ratings.is_ig(r["Рейтинг ранее"]) and ratings.is_ig(r["Рейтинг сейчас"]) is False:
            f.append("переход из IG в HY")
        if r["Статус"] == "осталась" and abs(r["Δ вес, п.п."]) >= c_cfg["weight_change_pp"]:
            f.append(f"вес {r['Δ вес, п.п.']:+.1f} п.п.")
        if pd.notna(r["Δ YTM, бп"]) and abs(r["Δ YTM, бп"]) >= c_cfg["ytm_change_bp"]:
            f.append(f"YTM {r['Δ YTM, бп']:+.0f} бп")
        flags.append("; ".join(f))
    pos["Изменения"] = flags
    order = {"новая": 0, "выбыла": 1, "осталась": 2}
    pos = pos.assign(_o=pos["Статус"].map(order), _f=pos["Изменения"].eq("")).sort_values(["_o", "_f"]) \
        .drop(columns=["_o", "_f"]).reset_index(drop=True)
    rating_changes = pos[pos["Δ рейтинга, ступеней"].fillna(0) != 0][
        ["Позиция", "Рейтинг ранее", "Рейтинг сейчас", "Δ рейтинга, ступеней", "Изменения"]]

    # Аллокация: цель — из текущего снимка (или унаследованная)
    alloc = None
    if a1.allocation is not None:
        prev_by = prev.positions.groupby("sleeve")["weight"].sum()
        alloc = a1.allocation.copy()
        alloc["Ранее"] = [float(prev_by.get(s, np.nan)) if isinstance(s, str) else np.nan
                          for s in alloc["Sleeve в портфеле"]]
        if a0.allocation is not None:     # sleeve мог называться в старом файле иначе
            old = a0.allocation.set_index("Sleeve")["Текущее"]
            alloc["Ранее"] = alloc["Ранее"].fillna(alloc["Sleeve"].map(old))
        alloc["Δ за период"] = alloc["Текущее"] - alloc["Ранее"]
        alloc = alloc[["Sleeve", "Цель", "Ранее", "Текущее", "Δ за период", "Отклонение", "Статус"]]

    # Концентрации: что изменило статус
    c1 = a1.concentration.set_index(["Тип", "Объект"])
    c0 = a0.concentration.set_index(["Тип", "Объект"])
    cc = []
    for key in c1.index.union(c0.index):
        s1 = c1["Статус"].get(key, "нет в портфеле")
        s0 = c0["Статус"].get(key, "нет в портфеле")
        d1 = c1["Доля"].get(key, 0.0)
        d0 = c0["Доля"].get(key, 0.0)
        if s1 != s0 and not (s1 == "в пределах" and s0 == "нет в портфеле") \
                and not (s0 == "в пределах" and s1 == "нет в портфеле"):
            cc.append({"Тип": key[0], "Объект": key[1], "Ранее": d0, "Сейчас": d1, "Статус ранее": s0, "Статус сейчас": s1})
    return Comparison(cur_date=cur.as_of, prev_date=prev.as_of, metrics=metrics, attribution=attribution,
                      allocation=alloc, positions=pos, rating_changes=rating_changes,
                      concentration_changes=pd.DataFrame(cc), notes=notes)
