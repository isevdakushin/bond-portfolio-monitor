"""Запуск анализа без интерфейса.

Пример:
    python run_report.py Main_Portfolio_14_09_26_v2.xlsx
    python run_report.py новый.xlsx --previous старый.xlsx   (сравнение — на следующих шагах)
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from pathlib import Path

import pandas as pd

from dotenv import load_dotenv

from core.analytics import analyze
from core.gspread import METHOD_LABEL, recompute
from core.pipeline import run as run_pipeline
from core.report import build as build_report
from core.rules import evaluate as evaluate_rules
from core.snapshots import compare as compare_snapshots
from core.snapshots import inherit_targets, order_snapshots
from core.signals import relative_value, spread_changes
from core.market import check_file_claims, curves_between, load_market
from core import corrections
from core.config import load_config
from core.loader import LoadError, load_snapshot
from core.quality import run_checks, summary


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Анализ портфеля еврооблигаций")
    ap.add_argument("file", help="Excel-файл портфеля (.xlsx)")
    ap.add_argument("--previous", help="предыдущий снимок для сравнения")
    ap.add_argument("--config", help="путь к config.yaml")
    ap.add_argument("--out", default="output", help="папка для результатов")
    ap.add_argument("--no-market", action="store_true", help="не загружать рыночные данные")
    ap.add_argument("--corrections", help="файл поправок аналитика (JSON, выгружается из интерфейса)")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    try:
        snap = load_snapshot(args.file)
    except LoadError as e:
        print(f"ОШИБКА ЗАГРУЗКИ: {e}")
        return 2

    print(f"Файл: {snap.source_name}")
    print(f"Дата снимка: {snap.as_of:%d.%m.%Y}" if snap.as_of else "Дата снимка: не определена")
    print(f"Позиций: {len(snap.positions)}; листы: {snap.sheet_names}")

    corr_log = None
    if args.corrections:
        try:
            items = corrections.from_json(Path(args.corrections).read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            print(f"Файл поправок не загружен: {e}")
            items = []
        snap, corr_log = corrections.apply(snap, items)
        print("\nПоправки аналитика (исходный файл не изменён):")
        for _, r in corr_log.iterrows():
            print(f"  {r['Позиция']} — {r['Поле']}: {r['Было']} → {r['Стало']} [{r['Статус']}]. Основание: {r['Основание']}")

    issues = run_checks(snap, cfg)
    s = summary(issues)
    print(f"\nПроверка качества данных: ошибок {s['Ошибка']}, предупреждений {s['Предупреждение']}, инфо {s['Инфо']}\n")
    with pd.option_context("display.max_colwidth", 200, "display.width", 250):
        for _, r in issues.iterrows():
            extra = ""
            if r["В файле"] or r["Расчёт / ожидание"]:
                extra = f"  [в файле: {r['В файле'] or '—'} | расчёт/ожидание: {r['Расчёт / ожидание'] or '—'}]"
            pos = f"{r['Позиция']}" + (f" / {r['Поле']}" if r["Поле"] else "")
            print(f"[{r['Уровень']}] {r['Категория']} — {pos}: {r['Комментарий']}{extra}")

    an = analyze(snap, cfg)
    print("\nАналитика: метрики против цели")
    for _, r in an.targets.iterrows():
        pct_like = r["key"].startswith("ytm")
        f = (lambda x: f"{x * 100:.2f}%") if pct_like else (lambda x: f"{x:.2f}")
        pf = r.get("Pro forma при целевой аллокации")
        print(f"  {r['Метрика']}: {f(r['Текущее'])} | цель {f(r['Цель']) if pd.notna(r['Цель']) else '—'} | "
              f"{r['Статус']}" + (f" | pro forma {f(pf)}" if pd.notna(pf) else ""))
    if an.allocation is not None:
        print("Аллокация (отклонения сверх порога):")
        for _, r in an.allocation[an.allocation["Статус"] != "в пределах"].iterrows():
            print(f"  {r['Sleeve']}: {r['Текущее'] * 100:.1f}% против цели {r['Цель'] * 100:.1f}% "
                  f"({r['Отклонение'] * 100:+.1f} п.п., {r['Статус']})")
    print("Концентрации (превышения и близко к лимиту):")
    for _, r in an.concentration[an.concentration["Статус"] != "в пределах"].iterrows():
        print(f"  {r['Тип']} {r['Объект']}: {r['Доля'] * 100:.1f}% при лимите {r['Лимит'] * 100:.1f}% — {r['Статус']}")

    prev = None
    if args.previous:
        try:
            prev = load_snapshot(args.previous)
        except LoadError as e:
            print(f"\nПредыдущий снимок не загружен: {e}")
    if prev is not None:
        snap, prev, note = order_snapshots(snap, prev)
        snap, inh = inherit_targets(snap, prev)
        for n in ([note] if note else []) + inh:
            print(f"\nПРИМЕЧАНИЕ: {n}")
    rv = relative_value(snap, cfg)
    print(f"\nСигналы по спредам (пиры: рейтинговая группа ± {cfg['signals']['duration_window']:g} г. дюрации; "
          f"наклон {rv.attrs.get('slope', 0):.1f} бп/год)")
    for _, r in rv[rv["Сигнал"].isin(["шире группы", "уже группы"])].iterrows():
        rel = "" if r["Надёжность"] == "достаточно пиров" else f" [{r['Надёжность']}]"
        print(f"  {r['Сигнал']}: {r['Позиция']} ({r['Рейтинг']}) — {r['G-spread']:.0f} бп против медианы пиров "
              f"{r['Медиана пиров']:.0f} бп ({r['Премия, бп']:+.0f} бп){rel}")
    if prev is not None:
        ch = spread_changes(snap, prev, cfg)
        print(f"Изменение спредов с {prev.as_of:%d.%m.%Y}:" if prev.as_of else "Изменение спредов:")
        for _, r in ch[ch["Сигнал"] != "в норме"].iterrows():
            print(f"  {r['Сигнал']}: {r['Позиция']} {r['Изменение, бп']:+.0f} бп (группа {r['Медианное изменение группы, бп']:+.0f} бп)")

    if not args.no_market:
        load_dotenv(Path(__file__).parent / ".env")
        mk = load_market(snap.as_of, (os.environ.get("FRED_API_KEY") or "").strip() or None)
        print("\nРыночные данные")
        for e in mk.errors:
            print(f"  НЕТ ДАННЫХ: {e}")
        for label, c in (("на дату снимка", mk.curve_asof), ("последняя", mk.curve_latest)):
            if c:
                print(f"  10Y UST {label}: {c.at(10) * 100:.2f}% (наблюдение {c.date:%d.%m.%Y}; {c.how}; {c.url})")
        for label, v in (("на дату снимка", mk.hy_asof), ("последний", mk.hy_latest)):
            if v and v[0]:
                print(f"  HY OAS {label}: {v[1]:.0f} бп (наблюдение {v[0]:%d.%m.%Y}; {mk.hy_meta.get('channel')}; "
                      f"{mk.hy_meta.get('url')})")
        for c in check_file_claims(snap.dashboard_texts, mk):
            print(f"  Сверка файла: «{c['context']}» — в файле {c['stated'] * 100:.2f}%, Treasury "
                  f"{c['official'] * 100:.2f}% ({c['diff_bp']:+.0f} бп)")

        ev = evaluate_rules(snap, mk, cfg)
        print("\nПравила добора (из файла; уровни — Treasury/FRED). Решение остаётся за аналитиком.")
        for _, r in ev["table"].iterrows():
            print(f"  {r['Правило']}: {r['Статус']} | на дату снимка {r['На дату снимка']} | сейчас {r['Сейчас']} | "
                  f"касание: {r['Касание с даты снимка']}")
        for a in ev["actions"]:
            print(f"  ДЕЙСТВИЕ [{a['Приоритет']}]: {a['Действие']}. Основание: {a['Основание']}. "
                  f"Финансирование: {a['Финансирование']}")

        if mk.curve_asof is not None:
            win = cfg["gspread"]["implied_date_window_days"]
            gs = recompute(snap, mk.curve_asof, cfg, curves_between(snap.as_of - dt.timedelta(days=win), snap.as_of)
                           if snap.as_of else None)
            print(f"\nПересчёт G-spread (кривая Treasury на {mk.curve_asof.date:%d.%m.%Y}, основной способ — "
                  f"{METHOD_LABEL[gs.primary]})")
            print(f"  Средневзвешенный G-spread: файл {gs.portfolio['file']:.0f} бп, пересчёт "
                  f"{gs.portfolio['recomputed']:.0f} бп")
            for _, r in gs.summary.iterrows():
                print(f"  {r['Способ']}: среднее расхождение {r['Среднее расхождение, бп']:+.1f} бп, "
                      f"средний модуль {r['Средний модуль, бп']:.1f} бп, разброс {r['Разброс (σ), бп']:.1f} бп")
            if gs.systematic["flag"]:
                print(f"  СИСТЕМНОЕ РАСХОЖДЕНИЕ: медиана {gs.systematic['median']:+.0f} бп по всем бумагам")
            for m, v in gs.implied.items():
                if m in METHOD_LABEL:
                    print(f"  Диагностика: лучше всего файл объясняется кривой на {v['date']:%d.%m.%Y} "
                          f"{METHOD_LABEL[m]} (средний модуль {v['mae']:.1f} бп)")
            for _, r in gs.table[gs.table["Статус"] == "выброс"].iterrows():
                print(f"  Выброс: {r['Позиция']} — отклонение от общего {r['Отклонение от общего, бп']:+.0f} бп")

    if prev is not None:
        key = (os.environ.get("FRED_API_KEY") or "").strip() or None
        c_cur = c_prev = None
        if not args.no_market:
            c_cur, c_prev = load_market(snap.as_of, key).curve_asof, load_market(prev.as_of, key).curve_asof
        cmp = compare_snapshots(snap, prev, cfg, c_cur, c_prev)
        print(f"\nСравнение снимков: {snap.as_of:%d.%m.%Y} против {prev.as_of:%d.%m.%Y}")
        for _, r in cmp.metrics.iterrows():
            if r["Изменение"] is None or pd.isna(r["Изменение"]):
                continue
            ch = r["Изменение"]
            s = {"pct": f"{ch * 10000:+.0f} бп", "share": f"{ch * 100:+.1f} п.п.", "bp": f"{ch:+.1f} бп",
                 "num": f"{ch:+.2f}", "int": f"{ch:+.0f}",
                 "txt": f"{ch:+.2f} ступени ({'хуже' if ch > 0 else 'лучше' if ch < 0 else 'без изменений'})"}[r["Тип"]]
            print(f"  {r['Метрика']}: {s}")
        print("  Разложение изменения YTM портфеля, бп:")
        for _, r in cmp.attribution["YTM, бп"].iterrows():
            print(f"    {r['Компонент']}: {r['Вклад']:+.1f}")
        for _, r in cmp.positions[cmp.positions["Изменения"] != ""].iterrows():
            print(f"  {r['Позиция']}: {r['Изменения']}")
        for n in cmp.notes:
            print(f"  ПРИМЕЧАНИЕ: {n}")

    out = Path(args.out)
    out.mkdir(exist_ok=True)
    stem = snap.as_of.strftime("%Y-%m-%d") if snap.as_of else Path(args.file).stem
    path = out / f"quality_{stem}.csv"
    issues.to_csv(path, index=False, encoding="utf-8-sig")
    print(f"\nТаблица проверок сохранена: {path}")
    res = run_pipeline(snap, cfg, prev=prev, fred_key=(os.environ.get("FRED_API_KEY") or "").strip() or None,
                       use_market=not args.no_market)
    if res.estimate is not None:
        pe = res.estimate.portfolio
        print(f"\nОценка на {res.estimate.est_date:%d.%m.%Y} по индексам FRED (оценка, не данные): YTM ≈ "
              f"{pe['ytm_est'] * 100:.2f}% ({(pe['ytm_est'] - pe['ytm_file']) * 1e4:+.0f} бп), G-spread ≈ {pe['gs_est']:.0f} бп; "
              f"цена {pe['price'] * 100:+.2f}%, купон {pe['carry'] * 100:+.2f}%, итого {pe['total'] * 100:+.2f}% NAV")
    print("Стресс-тест (P&L, % NAV):")
    for _, r in res.stress["summary"].iterrows():
        print(f"  {r['Сценарий']}: {r['P&L портфеля, % NAV'] * 100:+.2f}%")
    res.corrections = corr_log
    rep = build_report(res)
    rpath = out / f"report_{stem}.html"
    rpath.write_text(rep.html, encoding="utf-8")
    print(f"Отчёт сохранён: {rpath} (версия текста: {rep.version}; PDF — кнопкой «Печать / PDF» в браузере)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
