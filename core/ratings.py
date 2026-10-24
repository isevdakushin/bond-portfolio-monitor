"""Рейтинговая шкала S&P/Fitch и разбор поля Credit bucket."""
from __future__ import annotations

import re

SCALE = ["AAA", "AA+", "AA", "AA-", "A+", "A", "A-", "BBB+", "BBB", "BBB-",
         "BB+", "BB", "BB-", "B+", "B", "B-", "CCC+", "CCC", "CCC-", "CC", "C", "D"]
LETTER_GRADES = {"AAA", "AA", "A", "BBB", "BB", "B", "CCC", "CC", "C", "D"}
IG_FLOOR = "BBB-"


MOODYS = {"Aaa": "AAA", "Aa1": "AA+", "Aa2": "AA", "Aa3": "AA-", "A1": "A+", "A2": "A", "A3": "A-",
          "Baa1": "BBB+", "Baa2": "BBB", "Baa3": "BBB-", "Ba1": "BB+", "Ba2": "BB", "Ba3": "BB-",
          "B1": "B+", "B2": "B", "B3": "B-", "Caa1": "CCC+", "Caa2": "CCC", "Caa3": "CCC-", "Ca": "CC", "C": "C"}


def normalize(rating) -> str | None:
    """Рейтинг к шкале S&P/Fitch. Понимает Moody's (Baa2 -> BBB) и строки вида 'BBB+/Baa1', 'A- (Fitch)'.
    NR/WR и прочее нераспознанное -> None."""
    if rating is None:
        return None
    raw = str(rating).strip()
    if not raw or raw.lower() == "nan":
        return None
    s = raw.upper().replace(" ", "")
    if s in SCALE:
        return s
    for t in re.split(r"[\s/|;,()]+", raw):
        if not t:
            continue
        if t in MOODYS:
            return MOODYS[t]
        tu = t.upper().rstrip("*")
        if tu in SCALE:
            return tu
    return None


def composite(values) -> str | None:
    """Составной рейтинг из нескольких агентств (правило индексов ICE/Bloomberg):
    из трёх — средний, из двух — худший, из одного — он сам."""
    scores = sorted(SCALE.index(r) for r in (normalize(v) for v in values) if r)
    if not scores:
        return None
    if len(scores) >= 3:
        return SCALE[scores[len(scores) // 2]]
    return SCALE[scores[-1]]


def score(rating) -> int | None:
    """1 = AAA, 10 = BBB-, 11 = BB+ ... Меньше — лучше."""
    r = normalize(rating)
    return SCALE.index(r) + 1 if r else None


def is_ig(rating) -> bool | None:
    s = score(rating)
    return None if s is None else s <= SCALE.index(IG_FLOOR) + 1


def letter(rating) -> str | None:
    """Буквенная ступень без модификатора: BBB+ -> BBB."""
    r = normalize(rating)
    return r.rstrip("+-") if r else None


def group(rating) -> str | None:
    """Укрупнённая рейтинговая группа для статистики спредов."""
    l = letter(rating)
    if l is None:
        return None
    if l in ("AAA", "AA"):
        return "AA и выше"
    if l in ("CCC", "CC", "C", "D"):
        return "CCC и ниже"
    return l


def parse_bucket(bucket) -> dict:
    """Разбор Credit bucket: 'A/BBB / EM quasi' -> grades {A, BBB}; флаги IG/HY/Govt/fund.

    Буквенными ступенями считаются только токены, написанные заглавными буквами,
    чтобы не путать с обычными словами.
    """
    text = "" if bucket is None else str(bucket)
    tokens = [t for t in re.split(r"[^A-Za-z]+", text) if t]
    grades = {t for t in tokens if t in LETTER_GRADES}
    upper = {t.upper() for t in tokens}
    return {
        "grades": grades,
        "ig": "IG" in upper,
        "hy": "HY" in upper,
        "govt": bool(upper & {"GOVT", "GOV", "SOVEREIGN", "UST"}),
        "fund": bool(upper & {"FUND", "ETF"}),
    }
