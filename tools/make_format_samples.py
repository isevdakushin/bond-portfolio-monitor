"""Генерирует в папке samples/ примеры файлов портфеля в других форматах (из исходного файла компании).

Нужны для демонстрации, что инструмент читает файл другой структуры:
    python tools/make_format_samples.py
"""
import csv
import datetime as dt
import sys
from pathlib import Path

from openpyxl import Workbook

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.loader import load_snapshot  # noqa: E402
from core.ratings import MOODYS  # noqa: E402

TO_MOODYS = {v: k for k, v in MOODYS.items()}


def main():
    base = load_snapshot(ROOT / "Main_Portfolio_14_09_26_v2.xlsx").positions
    out = ROOT / "samples"
    out.mkdir(exist_ok=True)
    d = lambda x: x.strftime("%d.%m.%Y") if isinstance(x, dt.date) else ""  # noqa: E731

    # 1. CSV из русского Excel: «;», десятичная запятая, проценты и «бп» в ячейках.
    with open(out / "1_portfolio_2026-09-14.csv", "w", newline="", encoding="cp1251") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["ISIN", "Наименование", "Доля, %", "Доходность к погашению", "Спред к UST", "Дюрация",
                    "Дата погашения", "Рейтинг", "Сегмент", "Страна", "Отрасль"])
        for _, r in base.iterrows():
            w.writerow([r["isin"] or "", r["issuer"], f"{r['weight'] * 100:.2f}".replace(".", ","),
                        f"{r['ytm'] * 100:.3f}%".replace(".", ","), f"{r['gspread']:.0f} бп",
                        f"{r['mod_duration']:.2f}".replace(".", ","), d(r["maturity"]), r["rating"], r["sleeve"],
                        r["country"], r["sector"]])

    # 2. Отчёт с разделами вместо колонки Sleeve, двухстрочным заголовком, рыночной стоимостью,
    #    рейтингами S&P и Moody's и промежуточными итогами.
    wb = Workbook()
    ws = wb.active
    ws.title = "Отчёт"
    ws.append(["Портфель еврооблигаций по состоянию на 14.09.2026"])
    ws.append([])
    ws.append(["Бумага", "ISIN", "Рыночная стоимость", "Доходность и риск", None, None, "Погашение", "S&P", "Moody's"])
    ws.append([None, None, None, "YTM", "G-spread", "Mod. duration", None, None, None])
    for sleeve, g in base.groupby("sleeve", sort=False):
        ws.append([sleeve])
        for _, r in g.iterrows():
            ws.append([r["issuer"], r["isin"], r["weight"] * 17_600_000, r["ytm"], r["gspread"], r["mod_duration"],
                       r["maturity"], r["rating"], TO_MOODYS.get(r["rating"])])
        ws.append(["Итого по сегменту", None, g["weight"].sum() * 17_600_000])
    ws.append(["Итого", None, 17_600_000])
    wb.save(out / "2_report_sections_mv.xlsx")

    # 3. Колонка доходности с нестандартным названием — нужно ручное сопоставление.
    wb = Workbook()
    ws = wb.active
    ws.title = "Data"
    ws.append(["Issuer", "Weight", "Доход, %", "G-spread", "Mod. duration", "Maturity", "Rating", "Sleeve"])
    for _, r in base.iterrows():
        ws.append([r["issuer"], r["weight"], r["ytm"] * 100, r["gspread"], r["mod_duration"], r["maturity"],
                   r["rating"], r["sleeve"]])
    wb.save(out / "3_unknown_column_14_09_26.xlsx")
    print("Готово:", ", ".join(p.name for p in sorted(out.iterdir())))


if __name__ == "__main__":
    main()
