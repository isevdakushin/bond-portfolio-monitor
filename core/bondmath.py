"""Облигационная математика для проверок (без внешних данных — только поля из файла)."""
from __future__ import annotations

import datetime as dt
import re

import pandas as pd


def _add_months(d: dt.date, months: int) -> dt.date:
    return (pd.Timestamp(d) + pd.DateOffset(months=months)).date()


def modified_duration(settle: dt.date, maturity: dt.date, coupon: float, ytm: float, freq: int = 2):
    """Модифицированная дюрация буллет-облигации с полугодовым купоном.

    Денежные потоки строятся назад от даты погашения с шагом 12/freq месяцев.
    Время — в годах (факт/365,25), дисконтирование (1 + y/freq)^(−freq·t).
    Опционы (call, make-whole) не учитываются.
    """
    if not (settle and maturity and coupon is not None and ytm is not None) or maturity <= settle:
        return None
    step = 12 // freq
    dates, d, k = [], maturity, 0
    while d > settle:
        dates.append(d)
        k += 1
        d = _add_months(maturity, -step * k)
    dates.reverse()
    cfs = [coupon / freq * 100] * len(dates)
    cfs[-1] += 100
    t = [(x - settle).days / 365.25 for x in dates]
    dfs = [(1 + ytm / freq) ** (-freq * ti) for ti in t]
    pv = sum(c * f for c, f in zip(cfs, dfs))
    mac = sum(ti * c * f for ti, c, f in zip(t, cfs, dfs)) / pv
    return mac / (1 + ytm / freq)


def years_between(a: dt.date, b: dt.date) -> float:
    return (b - a).days / 365.25


def isin_valid(isin) -> bool:
    """Формат ISIN и контрольная цифра (алгоритм Луна по ISO 6166)."""
    s = str(isin or "").strip().upper()
    if not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}[0-9]", s):
        return False
    digits = "".join(str(int(ch, 36)) for ch in s[:-1])
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 0:
            d *= 2
            d = d - 9 if d > 9 else d
        total += d
    return (10 - total % 10) % 10 == int(s[-1])
