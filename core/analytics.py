"""Аналитика портфеля: метрики, сравнение с целями, распределения, аллокация, концентрации.

Все метрики — средневзвешенные по весам позиций из файла (Σ w·x по позициям, где x заполнен).
Никаких внешних данных: только поля файла и пороги из config.yaml.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from . import ratings
from .loader import Snapshot


@dataclass
class Analysis:
    metrics: dict                     # ключевые метрики портфеля
    targets: pd.DataFrame             # метрика, текущее, цель, отклонение, статус, pro forma
    allocation: pd.DataFrame | None   # sleeve, цель, текущее, отклонение, статус
    breakdowns: dict                  # измерение -> DataFrame
    concentration: pd.DataFrame       # проверки лимитов
    positions: pd.DataFrame           # позиции с вкладами в метрики
    proforma: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)


def _wavg(df, col):
    m = df["weight"].notna() & df[col].notna()
    w = df.loc[m, "weight"].sum()
    return float((df.loc[m, "weight"] * df.loc[m, col]).sum() / w) if w else None


def _wsum(df, col):
    m = df["weight"].notna() & df[col].notna()
    return float((df.loc[m, "weight"] * df.loc[m, col]).sum())


def sector_group(s):
    return str(s).split("/")[0].strip() if s else None


def _prepare(p: pd.DataFrame) -> pd.DataFrame:
    p = p.copy()
    p["rating_norm"] = p["rating"].map(ratings.normalize)
    p["rating_score"] = p["rating"].map(ratings.score)
    p["rating_group"] = p["rating"].map(ratings.group)
    def _cls(r):
        if r["instrument_type"] == "cash":
            return "Денежные средства / T-bills"
        ig = ratings.is_ig(r["rating"])
        return "Рейтинг не указан" if ig is None else ("IG" if ig else "HY")
    p["ig_hy"] = p.apply(_cls, axis=1)
    p["sector_group"] = p["sector"].map(sector_group)
    p["issuer_key"] = p["issuer_short"]
    # Вклады в портфельные метрики: w·x. DTS (duration × spread) — мера кредитного риска:
    # потеря в % NAV при относительном расширении спредов на 1% ≈ Σ w·MD·S / 100 / 100.
    p["ytm_contrib"] = p["weight"] * p["ytm"]
    p["md_contrib"] = p["weight"] * p["mod_duration"]
    p["gs_contrib"] = p["weight"] * p["gspread"]
    p["dts"] = p["mod_duration"] * p["gspread"]
    p["dts_contrib"] = p["weight"] * p["dts"]
    return p


def _breakdown(p, col, label):
    rows = []
    for key, g in p.groupby(p[col].fillna("— не указано"), sort=False):
        rows.append({label: key, "Вес": g["weight"].sum(), "Позиций": len(g),
                     "YTM": _wavg(g, "ytm"), "Mod. duration": _wavg(g, "mod_duration"),
                     "G-spread, бп": _wavg(g, "gspread"),
                     "Вклад в YTM": _wsum(g, "ytm"), "Вклад в дюрацию": _wsum(g, "mod_duration"),
                     "Доля DTS": _wsum(g, "dts")})
    df = pd.DataFrame(rows)
    total_dts = df["Доля DTS"].sum()
    df["Доля DTS"] = df["Доля DTS"] / total_dts if total_dts else None
    return df.sort_values("Вес", ascending=False).reset_index(drop=True)


def _avg_rating(p):
    m = p["rating_score"].notna() & p["weight"].notna()
    if not m.any():
        return None, None
    s = float((p.loc[m, "weight"] * p.loc[m, "rating_score"]).sum() / p.loc[m, "weight"].sum())
    return s, ratings.SCALE[int(round(s)) - 1]


def analyze(snap: Snapshot, cfg: dict) -> Analysis:
    a_cfg, c_cfg = cfg["analysis"], cfg["concentration"]
    p = _prepare(snap.positions)
    notes = []

    ytm = _wsum(p, "ytm")
    md = _wsum(p, "mod_duration")
    gs = _wsum(p, "gspread")
    avg_score, avg_rating = _avg_rating(p)
    non_cash = p[p["instrument_type"] != "cash"]
    issuers = non_cash.groupby("issuer_key")["weight"].sum().sort_values(ascending=False)
    shares = issuers / issuers.sum() if issuers.sum() else issuers
    hhi = float((shares ** 2).sum()) if len(shares) else None

    metrics = {
        "ytm_gross": ytm,
        "ytm_net": ytm - a_cfg["fee"],
        "mod_duration": md,
        "gspread": gs,
        "dts": _wsum(p, "dts"),
        "avg_rating": avg_rating,
        "avg_rating_score": avg_score,
        "ig_share": float(p.loc[p["ig_hy"] == "IG", "weight"].sum()),
        "hy_share": float(p.loc[p["ig_hy"] == "HY", "weight"].sum()),
        "cash_share": float(p.loc[p["instrument_type"] == "cash", "weight"].sum()),
        "em_share": float(p.loc[p["region"].fillna("").str.upper() == "EM", "weight"].sum()),
        "n_positions": len(p),
        "n_issuers": int(len(issuers)),
        "top5_issuers_share": float(issuers.head(5).sum()),
        "effective_issuers": 1 / hhi if hhi else None,
        "fee": a_cfg["fee"],
    }

    targets = _targets(snap, metrics, a_cfg)
    allocation, proforma = _allocation(snap, p, a_cfg)
    if proforma:
        targets["Pro forma при целевой аллокации"] = targets["key"].map(proforma)
        notes.append("Pro forma — метрики портфеля, если доли sleeve привести к целевым, сохранив средние "
                     "метрики бумаг внутри каждого sleeve. Это оценка, а не прогноз.")

    breakdowns = {
        "Sleeve": _breakdown(p, "sleeve", "Sleeve"),
        "Рейтинг": _breakdown(p.assign(_r=p["rating_norm"]).sort_values("rating_score"), "_r", "Рейтинг"),
        "Рейтинговая группа": _breakdown(p, "rating_group", "Рейтинговая группа"),
        "IG / HY": _breakdown(p, "ig_hy", "Класс"),
        "Регион": _breakdown(p, "region", "Регион"),
        "Страна": _breakdown(p, "country", "Страна"),
        "Сектор (укрупнённо)": _breakdown(p, "sector_group", "Сектор"),
        "Сектор (детально)": _breakdown(p, "sector", "Сектор"),
        "Эмитент": _breakdown(p, "issuer_key", "Эмитент"),
    }
    # Рейтинги — в порядке шкалы, а не по весу.
    br = breakdowns["Рейтинг"]
    br["_s"] = br["Рейтинг"].map(lambda r: ratings.score(r) or 99)
    breakdowns["Рейтинг"] = br.sort_values("_s").drop(columns="_s").reset_index(drop=True)

    concentration = _concentration(p, snap, metrics, c_cfg, allocation)
    return Analysis(metrics=metrics, targets=targets, allocation=allocation, breakdowns=breakdowns,
                    concentration=concentration, positions=p, proforma=proforma, notes=notes)


METRIC_DEFS = [
    ("ytm_gross", "YTM брутто", "ytm"),
    ("ytm_net", "YTM после комиссии", "ytm"),
    ("mod_duration", "Mod. duration, лет", "duration"),
    ("gspread", "G-spread, бп", "gspread_bp"),
]


def _targets(snap, metrics, a_cfg):
    rows = []
    for key, label, tkey in METRIC_DEFS:
        t = snap.dashboard_metrics.get(key, {}).get("target")
        cur = metrics[key]
        gap = None if t is None else cur - t
        tol = a_cfg["target_tolerance"][tkey]
        if gap is None:
            status = "цель не задана"
        elif abs(gap) <= tol:
            status = "в пределах цели"
        else:
            status = "ниже цели" if gap < 0 else "выше цели"
        rows.append({"key": key, "Метрика": label, "Текущее": cur, "Цель": t, "Отклонение": gap,
                     "Допуск": tol, "Статус": status})
    return pd.DataFrame(rows)


def _allocation(snap, p, a_cfg):
    a = snap.allocation
    if a is None or a.empty:
        return None, {}
    by_sleeve = p.groupby("sleeve")
    rows, pf = [], {"ytm_gross": 0.0, "mod_duration": 0.0, "gspread": 0.0}
    covered = 0.0
    for _, r in a.iterrows():
        ps = r.get("portfolio_sleeve")
        g = by_sleeve.get_group(ps) if ps in by_sleeve.groups else None
        cur = float(g["weight"].sum()) if g is not None else 0.0
        gap = cur - r["target"]
        thr = a_cfg["allocation_gap_pp"] / 100
        status = "в пределах" if abs(gap) <= thr else ("перевес" if gap > 0 else "недовес")
        rows.append({"Sleeve": r["sleeve"], "Sleeve в портфеле": ps, "Цель": r["target"], "Текущее": cur,
                     "Отклонение": gap, "Статус": status})
        if g is not None and r["target"]:
            for k, col in (("ytm_gross", "ytm"), ("mod_duration", "mod_duration"), ("gspread", "gspread")):
                v = _wavg(g, col)
                if v is not None:
                    pf[k] += r["target"] * v
            covered += r["target"]
    proforma = {}
    if abs(covered - 1) < 1e-6:
        proforma = dict(pf)
        proforma["ytm_net"] = pf["ytm_gross"] - a_cfg["fee"]
    return pd.DataFrame(rows), proforma


def _status(value, limit, band):
    if limit is None:
        return "—"
    if value > limit + 1e-9:
        return "превышен"
    if value >= band * limit:
        return "близко к лимиту"
    return "в пределах"


def _concentration(p, snap, metrics, c, allocation):
    rows = []
    band = c["warn_band"]

    def add(kind, name, value, limit, basis):
        rows.append({"Тип": kind, "Объект": name, "Доля": value, "Лимит": limit,
                     "Статус": _status(value, limit, band), "Основание лимита": basis})

    eligible = p[(p["instrument_type"] == "bond")]
    for name, w in eligible.groupby("issuer_key")["weight"].sum().sort_values(ascending=False).items():
        add("Эмитент", name, float(w), c["issuer_max"], "config: issuer_max (UCITS 5/10/40)")
    home = set(c["home_countries"])
    for name, w in p[p["instrument_type"] != "cash"].groupby("country")["weight"].sum() \
            .sort_values(ascending=False).items():
        if name in home:
            continue
        add("Страна", name, float(w), c["country_max"], "config: country_max (кроме " + ", ".join(home) + ")")
    for name, w in p[p["instrument_type"] != "cash"].groupby("sector_group")["weight"].sum() \
            .sort_values(ascending=False).items():
        add("Сектор (укрупнённо)", name, float(w), c["sector_group_max"], "config: sector_group_max")
    add("Регион", "EM всего", metrics["em_share"], c["em_total_max"], "дашборд компании: лимит EM ~30%")

    hy_limit, hy_basis = c["hy_total_max_fallback"], "config: hy_total_max_fallback"
    if allocation is not None:
        hy_rows = allocation[allocation["Sleeve"].str.contains(r"\bHY\b", regex=True)]
        if not hy_rows.empty:
            hy_limit = float(hy_rows["Цель"].sum())
            hy_basis = "сумма целей HY-сегментов листа Allocation: " + ", ".join(hy_rows["Sleeve"])
    add("Кредитное качество", "HY всего (ниже BBB-)", metrics["hy_share"], hy_limit, hy_basis)

    df = pd.DataFrame(rows)
    order = {"превышен": 0, "близко к лимиту": 1, "в пределах": 2, "—": 3}
    df["_o"] = df["Статус"].map(order)
    return df.sort_values(["_o", "Доля"], ascending=[True, False]).drop(columns="_o").reset_index(drop=True)
