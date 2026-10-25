"""Форматирование чисел для текста (десятичная запятая) с реестром всех выведенных чисел.

Реестр нужен отчёту: тест проверяет, что в записке нет чисел вне расчёта. Интерфейс использует
тот же форматтер и реестр просто не читает.
"""
from __future__ import annotations

import re


class Fmt:
    def __init__(self):
        self.numbers: set[str] = set()
        self.quotes: list[str] = []

    def _reg(self, s: str) -> str:
        for tok in re.findall(r"[+\-−]?\d+(?:[.,]\d+)?", s):
            self.numbers.add(tok.lstrip("+-−"))
        return s

    def num(self, x, d=2, sign=False):
        s = f"{x:+.{d}f}" if sign else f"{x:.{d}f}"
        return self._reg(s.replace(".", ",").replace("-", "−"))

    def pct(self, x, d=2, sign=False):
        return self.num(x * 100, d, sign) + "%"

    def pctg(self, x):
        """Процент без лишних нулей: 0.15 -> «15%», 0.045 -> «4,5%»."""
        v = round(x * 100, 2)
        d = 0 if abs(v - round(v)) < 1e-9 else 1 if abs(v * 10 - round(v * 10)) < 1e-9 else 2
        return self.num(v, d) + "%"

    def pp(self, x, d=1, sign=True):
        return self.num(x * 100, d, sign) + " п.п."

    def bp(self, x, sign=False, d=0):
        return self.num(x, d, sign) + " б.п."

    def bp_from(self, x, sign=True, d=0):
        return self.bp(x * 10000, sign, d)

    def int(self, x):
        return self._reg(f"{int(round(x))}")

    def label(self, text: str) -> str:
        """Постоянная подпись с цифрами (срок «10-летних UST», «UCITS 5/10/40»): не расчёт, но и не выдумка."""
        return self._reg(text)

    @staticmethod
    def date(d):
        return d.strftime("%d.%m.%Y") if d else "—"

    def quote(self, text: str) -> str:
        """Готовый текст из расчётных модулей или цитата из файла: десятичная точка -> запятая (даты не трогаются)."""
        text = re.sub(r"(?<![\d.])(\d+)\.(\d+)(?![\d.])", r"\1,\2", str(text))
        self.quotes.append(text)
        return text
