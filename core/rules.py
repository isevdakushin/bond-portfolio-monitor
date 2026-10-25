"""Правила добора из дашборда компании: разбор текста и проверка по рыночным уровням.

Правила компании (00 Dashboard, 14.09.2026):
- лестница длины: 10Y UST 5,10–5,15% → +5% NAV в Long IG; 10Y 5,25–5,40% → ещё +5% (до цели 25%);
- HY OAS ≥ 325 бп → IBHJ с 4,5% до 8%;
- календарные пересмотры в середине ноября и января: докупать по +5%, если уровни не пришли.

Правила читаются из текста файла (с цитатой); если в файле их нет — берутся из config.yaml.
Результат — статус каждого правила и действие из стратегии компании. Решение — за аналитиком.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field

import pandas as pd

from .loader import Snapshot

MONTHS_RU = {"январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма": 5, "июн": 6, "июл": 7, "август": 8,
             "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12}
_NUM = r"(\d+(?:[.,]\d+)?)"


def _f(x):
    return float(x.replace(",", "."))


@dataclass
class Rules:
    ladder: list                      # [{lower, upper, add, target_after}]
    hy_trigger_bp: float | None
    hy_instrument: str
    hy_from: float | None
    hy_to: float | None
    calendar: list                    # [(месяц, день)]
    calendar_add: float
    sleeve_keyword: str
    source: dict = field(default_factory=dict)   # правило -> "файл: «цитата»" или "config.yaml"


def _sentences(texts):
    for t in texts:
        for s in re.split(r"(?<=[.!?])\s+|\n", t):
            if s.strip():
                yield s.strip()


def parse_rules(snap: Snapshot, cfg: dict) -> Rules:
    rc = cfg["rules"]
    texts = list(snap.dashboard_texts) + [t for s, ts in snap.sheet_texts.items() for t in ts]
    texts = list(dict.fromkeys(texts))
    src = {}
    ladder, hy_bp, hy_from, hy_to, cal = [], None, None, None, []
    instrument = rc["hy_instrument"]
    if rc.get("prefer_file", True):
        for t in texts:
            # «10Y 5,10–5,15% → +5% NAV (увеличиваем длину до 20%)»
            for m in re.finditer(rf"10\s*-?\s*Y(?:\s*UST)?\s*{_NUM}\s*[–-]\s*{_NUM}\s*%\s*(?:→|->|=>)\s*\+?\s*{_NUM}\s*%"
                                 rf"(?:[^;.]*?до\s*{_NUM}\s*%)?", t, re.I):
                quote = m.group(0).strip()
                quote += ")" * max(0, quote.count("(") - quote.count(")"))
                step = {"lower": _f(m.group(1)) / 100, "upper": _f(m.group(2)) / 100, "add": _f(m.group(3)) / 100,
                        "target_after": _f(m.group(4)) / 100 if m.group(4) else None, "quote": quote}
                if not any(abs(s["lower"] - step["lower"]) < 1e-9 for s in ladder):
                    ladder.append(step)
            # «IBHJ 4,5% → 8% при расширении HY OAS до ≥325 бп»
            m = re.search(rf"([A-Z]{{3,5}})\s*{_NUM}\s*%\s*(?:→|->|=>)\s*{_NUM}\s*%[^.;]*?HY\s*OAS\D{{0,20}}?(\d{{2,4}})\s*бп",
                          t, re.I)
            if m and hy_bp is None:
                instrument, hy_from, hy_to, hy_bp = m.group(1), _f(m.group(2)) / 100, _f(m.group(3)) / 100, float(m.group(4))
                src["hy"] = f"файл: «{m.group(0).strip()}»"
        for s in _sentences(texts):
            sl = s.lower()
            if any(w in sl for w in ("смотрим", "пересмотр", "докупаем")) and not cal:
                found = []
                for stem, mo in MONTHS_RU.items():
                    if stem == "ма":
                        if re.search(r"\bма[йяе]\b", sl):
                            found.append(mo)
                    elif stem in sl:
                        found.append(mo)
                if found:
                    cal = [(mo, 15) for mo in sorted(set(found), key=lambda m: (m < 7, m))]
                    src["calendar"] = f"файл: «{s}»"
        if ladder:
            ladder.sort(key=lambda x: x["lower"])
            src["ladder"] = "файл: " + "; ".join(f"«{x['quote']}»" for x in ladder)
    if not ladder:
        ladder = [dict(x) for x in rc["ladder"]]
        src["ladder"] = "config.yaml (в файле правило не найдено)"
    if hy_bp is None:
        hy_bp, hy_from, hy_to = rc["hy_oas_trigger_bp"], rc["hy_from"], rc["hy_to"]
        src["hy"] = "config.yaml (в файле правило не найдено)"
    if not cal:
        cal = [tuple(int(x) for x in d.split("-")) for d in rc["calendar"]]
        src["calendar"] = "config.yaml (в файле правило не найдено)"
    return Rules(ladder=ladder, hy_trigger_bp=hy_bp, hy_instrument=instrument, hy_from=hy_from, hy_to=hy_to,
                 calendar=cal, calendar_add=rc["calendar_add"], sleeve_keyword=rc["ladder_sleeve_keyword"], source=src)


def _quote(snap: Snapshot, must: tuple, any_of: tuple = ()):
    """Предложение из текстов файла, содержащее все слова must и хотя бы одно из any_of."""
    texts = list(dict.fromkeys(list(snap.dashboard_texts) + [t for ts in snap.sheet_texts.values() for t in ts]))
    for s in _sentences(texts):
        for part in re.split(r";\s*", s):
            pl = part.lower()
            if all(w in pl for w in must) and (not any_of or any(w in pl for w in any_of)):
                return part.strip().rstrip(".")
    return None


def _overweights(snap: Snapshot, exclude_kw: str = "") -> str:
    a = snap.allocation
    if a is None or a.empty:
        return "целевой аллокации в файле нет"
    cur = snap.positions.groupby("sleeve")["weight"].sum()
    items = []
    for _, r in a.iterrows():
        c = float(cur.get(r.get("portfolio_sleeve"), 0.0))
        gap = c - r["target"]
        if gap > 1e-9 and exclude_kw not in str(r["sleeve"]).lower():
            items.append(f"{r['sleeve']} (+{_p(gap, 1)} п.п.)")
    return "за счёт сегментов в перевесе: " + ", ".join(items) if items else "перевесов нет"


def _funding(snap: Snapshot, must, any_of, exclude_kw):
    q = _quote(snap, must, any_of)
    return f"по стратегии: «{q}»" if q else _overweights(snap, exclude_kw)


def _p(x, d=2):
    """Процент с десятичной запятой: 0.0511 -> «5,11»."""
    return f"{x * 100:.{d}f}".replace(".", ",")


def _g(x):
    return f"{x * 100:g}".replace(".", ",")


def _step(row) -> str:
    """«Лестница, шаг 1: 10Y 5,10–5,15% → …» -> «шаг 1 (5,10–5,15%)»."""
    m = re.match(r"Лестница, (шаг \d+): 10Y ([\d,]+–[\d,]+%)", row["Правило"])
    return f"{m.group(1)} ({m.group(2)})" if m else row["Правило"]


def _first_touch(series: pd.Series | None, since: dt.date, level: float, above=True):
    if series is None or series.empty:
        return None
    s = series[series.index > since]
    hit = s[s >= level] if above else s[s <= level]
    return (hit.index[0], float(hit.iloc[0])) if not hit.empty else None


def next_reviews(calendar, after: dt.date, n=2):
    out = []
    for year in (after.year, after.year + 1, after.year + 2):
        for mo, day in calendar:
            d = dt.date(year, mo, day)
            if d > after:
                out.append(d)
    return sorted(out)[:n]


def evaluate(snap: Snapshot, market, cfg: dict, today: dt.date | None = None) -> dict:
    """Статус каждого правила на дату портфеля и на последнюю рыночную дату + действия по стратегии."""
    today = today or dt.date.today()
    rules = parse_rules(snap, cfg)
    p = snap.positions
    as_of = snap.as_of or today
    long_mask = p["sleeve"].fillna("").str.lower().str.contains(rules.sleeve_keyword)
    long_now = float(p.loc[long_mask, "weight"].sum())
    long_sleeve = p.loc[long_mask, "sleeve"].iloc[0] if long_mask.any() else None
    inst_mask = p["issuer"].fillna("").str.upper().str.contains(rules.hy_instrument.upper())
    hy_now = float(p.loc[inst_mask, "weight"].sum()) if inst_mask.any() else None

    ust_snap = market.curve_asof.at(10) if market.curve_asof else None
    ust_last = market.curve_latest.at(10) if market.curve_latest else None
    ust_last_date = market.curve_latest.date if market.curve_latest else None
    hy_snap = market.hy_asof[1] if market.hy_asof and market.hy_asof[0] else None
    hy_last, hy_last_date = (market.hy_latest[1], market.hy_latest[0]) if market.hy_latest and market.hy_latest[0] \
        else (None, None)

    fund_long = _funding(snap, ("long",), ("t-bill", "т-бил", "кэш", "cash"), rules.sleeve_keyword)
    fund_hy = _funding(snap, (rules.hy_instrument.lower(),), ("финансир", "сокращ"), rules.sleeve_keyword)
    rows, actions = [], []
    target = long_now
    any_ladder_now = False
    for i, step in enumerate(rules.ladder, start=1):
        touch = _first_touch(market.ust10_series, as_of, step["lower"])
        now = ust_last is not None and ust_last >= step["lower"]
        tgt_after = step.get("target_after")
        done = tgt_after is not None and long_now >= tgt_after - 1e-9
        if done:
            status = "выполнено (доля уже на уровне шага)"
        elif now:
            status = "сработало сейчас"
        elif touch:
            status = "уровень достигался, сейчас ниже"
        else:
            status = "не сработало"
        rows.append({
            "Правило": f"Лестница, шаг {i}: 10Y {_p(step['lower'])}–{_p(step['upper'])}% → "
                       f"+{_g(step['add'])}% NAV в Long IG" + (f" (до {_g(tgt_after)}%)" if tgt_after else ""),
            "Показатель": "10Y UST", "Порог": f"≥ {_p(step['lower'])}%",
            "На дату портфеля": f"{_p(ust_snap)}% ({as_of:%d.%m.%Y})" if ust_snap is not None else "нет данных",
            "Сейчас": f"{_p(ust_last)}% ({ust_last_date:%d.%m.%Y})" if ust_last is not None else "нет данных",
            "Касание с даты портфеля": f"{_p(touch[1])}% ({touch[0]:%d.%m.%Y})" if touch else "нет",
            "Статус": status, "Источник правила": rules.source.get("ladder", "")})
        if not done and (now or touch):
            any_ladder_now = any_ladder_now or now
            add_to = tgt_after if tgt_after is not None else target + step["add"]
            target = max(target, add_to)
    if target > long_now + 1e-9:
        reached = [r for r in rows if r["Статус"] in ("сработало сейчас", "уровень достигался, сейчас ниже")]
        if any_ladder_now:
            actions.append({"Приоритет": 1, "Действие": f"Довести {long_sleeve or 'Long IG'} с {_p(long_now, 1)}% "
                            f"до {_p(target, 1)}% NAV (+{_p(target - long_now, 1)} п.п.)",
                            "Основание": "10Y в зоне лестницы: " + "; ".join(_step(r) for r in reached),
                            "Финансирование": fund_long})
        else:
            actions.append({"Приоритет": 2, "Действие": f"Проверить добор {long_sleeve or 'Long IG'} до "
                            f"{_p(target, 1)}% NAV (+{_p(target - long_now, 1)} п.п.)",
                            "Основание": "10Y заходил в зоны лестницы после даты портфеля, сейчас ниже: "
                                         + "; ".join(f"{_step(r)} — касание {r['Касание с даты портфеля']}" for r in reached),
                            "Финансирование": fund_long + ". Если добор не сделан — при возврате 10Y в зону "
                                                          "или на календарном пересмотре"})

    # HY OAS → IBHJ
    if rules.hy_trigger_bp is not None:
        touch = _first_touch(market.hy_series, as_of, rules.hy_trigger_bp)
        now = hy_last is not None and hy_last >= rules.hy_trigger_bp
        done = hy_now is not None and rules.hy_to is not None and hy_now >= rules.hy_to - 1e-9
        status = ("выполнено (доля уже на целевом уровне)" if done else "сработало сейчас" if now
                  else "уровень достигался, сейчас ниже" if touch else "не сработало")
        rows.append({
            "Правило": f"HY OAS ≥ {rules.hy_trigger_bp:.0f} бп → {rules.hy_instrument} с {_g(rules.hy_from)}% "
                       f"до {_g(rules.hy_to)}% NAV",
            "Показатель": "HY OAS (ICE BofA, FRED)", "Порог": f"≥ {rules.hy_trigger_bp:.0f} бп",
            "На дату портфеля": f"{hy_snap:.0f} бп ({market.hy_asof[0]:%d.%m.%Y})" if hy_snap is not None else "нет данных",
            "Сейчас": f"{hy_last:.0f} бп ({hy_last_date:%d.%m.%Y})" if hy_last is not None else "нет данных",
            "Касание с даты портфеля": f"{touch[1]:.0f} бп ({touch[0]:%d.%m.%Y})" if touch else "нет",
            "Статус": status, "Источник правила": rules.source.get("hy", "")})
        if not done and (now or touch) and hy_now is not None:
            actions.append({"Приоритет": 1 if now else 2,
                            "Действие": f"Довести {rules.hy_instrument} с {_p(hy_now, 1)}% до {_g(rules.hy_to)}% "
                                        f"NAV (+{_p(rules.hy_to - hy_now, 1)} п.п.)" if now else
                                        f"Проверить, доведён ли {rules.hy_instrument} до {_g(rules.hy_to)}% NAV",
                            "Основание": f"HY OAS {hy_last:.0f} бп на {hy_last_date:%d.%m.%Y} выше триггера "
                                         f"{rules.hy_trigger_bp:.0f} бп"
                                         + (f"; триггер пробит {touch[0]:%d.%m.%Y}" if touch else ""),
                            "Финансирование": fund_hy})

    # Календарь
    reviews = next_reviews(rules.calendar, today)
    passed = [d for d in next_reviews(rules.calendar, as_of, n=6) if as_of < d <= today]
    levels_reached = any(r["Статус"] in ("сработало сейчас", "уровень достигался, сейчас ниже", "выполнено (доля уже на уровне шага)")
                         for r in rows if r["Показатель"] == "10Y UST")
    cal_status = (f"пересмотр {passed[-1]:%d.%m.%Y} прошёл" if passed else
                  f"следующий пересмотр {reviews[0]:%d.%m.%Y} (через {(reviews[0] - today).days} дн.)" if reviews else "—")
    rows.append({
        "Правило": "Календарь: " + ", ".join(f"{d:%d.%m}" for d in next_reviews(rules.calendar, as_of, n=len(rules.calendar)))
                   + f" — +{_g(rules.calendar_add)}% NAV в Long IG, если уровни не пройдены",
        "Показатель": "дата", "Порог": "дата пересмотра", "На дату портфеля": f"{as_of:%d.%m.%Y}",
        "Сейчас": f"{today:%d.%m.%Y}", "Касание с даты портфеля": "уровни лестницы пройдены" if levels_reached else "уровни не пройдены",
        "Статус": cal_status, "Источник правила": rules.source.get("calendar", "")})
    if passed and not levels_reached and long_now < (rules.ladder[-1].get("target_after") or 1):
        actions.append({"Приоритет": 1, "Действие": f"Календарный добор: +{_g(rules.calendar_add)}% NAV в Long IG",
                        "Основание": f"пересмотр {passed[-1]:%d.%m.%Y} прошёл, уровни лестницы не пройдены",
                        "Финансирование": fund_long})
    elif reviews and not actions:
        actions.append({"Приоритет": 3, "Действие": f"Следующий календарный пересмотр — {reviews[0]:%d.%m.%Y}",
                        "Основание": "уровни правил не пройдены", "Финансирование": "—"})

    return {"rules": rules, "table": pd.DataFrame(rows), "actions": sorted(actions, key=lambda a: a["Приоритет"]),
            "long_now": long_now, "hy_now": hy_now, "today": today, "next_reviews": reviews}
