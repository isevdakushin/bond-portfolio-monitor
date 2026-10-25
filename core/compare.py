"""Сопоставление позиций двух портфелей (используется в сигналах и сравнении портфелей)."""
from __future__ import annotations

import pandas as pd

from .loader import norm


def _empty(x) -> bool:
    return x is None or (isinstance(x, float) and pd.isna(x)) or (isinstance(x, str) and not x.strip())


def _keys(p: pd.DataFrame) -> pd.DataFrame:
    """Ключи сопоставления как object-колонки: пустое значение — "" (никогда не совпадает)."""
    p = p.copy()
    p["_isin"] = pd.Series(["" if _empty(x) else str(x).strip().upper() for x in p["isin"]], index=p.index, dtype=object)
    p["_name"] = pd.Series(["" if _empty(x) else norm(x) for x in p["issuer"]], index=p.index, dtype=object)
    p["_short_mat"] = pd.Series(["" if _empty(s) or _empty(m) else f"{norm(s)}|{m}"
                                 for s, m in zip(p["issuer_short"], p["maturity"])], index=p.index, dtype=object)
    return p


def match_positions(cur: pd.DataFrame, prev: pd.DataFrame) -> pd.DataFrame:
    """Пары позиций: по ISIN, затем по полному названию, затем по эмитенту + дате погашения.

    Возвращает таблицу с колонками текущего портфеля (без суффикса), предыдущего (суффикс _prev),
    способом сопоставления (match_by) и статусом: «осталась», «новая», «выбыла».
    """
    c, p = _keys(cur).reset_index(drop=True), _keys(prev).reset_index(drop=True)
    pairs, used_p = [], set()
    for key, label in (("_isin", "ISIN"), ("_name", "название"), ("_short_mat", "эмитент и погашение")):
        lookup = {}
        for j, v in p[key].items():
            if v and j not in used_p:
                lookup.setdefault(v, j)
        for i, v in c[key].items():
            if any(i == a for a, _, _ in pairs) or not v:
                continue
            j = lookup.get(v)
            if j is not None and j not in used_p:
                pairs.append((i, j, label))
                used_p.add(j)
    matched_c = {i for i, _, _ in pairs}
    rows = []
    cols = [x for x in cur.columns]
    for i, j, label in pairs:
        rec = {k: c.at[i, k] for k in cols}
        rec.update({f"{k}_prev": p.at[j, k] for k in cols})
        rec.update({"match_by": label, "Статус": "осталась"})
        rows.append(rec)
    for i in c.index:
        if i not in matched_c:
            rec = {k: c.at[i, k] for k in cols}
            rec.update({"match_by": None, "Статус": "новая"})
            rows.append(rec)
    for j in p.index:
        if j not in used_p:
            rec = {f"{k}_prev": p.at[j, k] for k in cols}
            rec.update({"label": p.at[j, "label"], "match_by": None, "Статус": "выбыла"})
            rows.append(rec)
    return pd.DataFrame(rows)
