"""Аналитическая записка (HTML, печать в PDF из браузера).

Текст собирается по шаблонам из рассчитанных значений, полными предложениями: что произошло, почему это
важно и что предлагаем сделать. Выводы по сигналам и правилам — из `core.narrative` (те же формулировки,
что в интерфейсе). Каждое число проходит через форматтер, который записывает его в реестр (`Report.numbers`):
тест проверяет, что в отчёте нет чисел вне реестра (кроме дат и цитат из файла компании).
Версия текста: «шаблон» (LLM не используется).
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field

import pandas as pd

from . import narrative as N
from .fmt import Fmt
from .gspread import METHOD_LABEL
from .market import SOURCE_HY, SOURCE_UST
from .pipeline import Results

TEXT_VERSION = "шаблон"
__all__ = ["Fmt", "Report", "build", "TEXT_VERSION"]

@dataclass
class Report:
    title: str
    html: str
    text: str
    numbers: set = field(default_factory=set)
    quotes: list = field(default_factory=list)
    version: str = TEXT_VERSION


def _e(s):
    return html.escape(str(s), quote=False)


def _table(head, rows, cls=""):
    th = "".join(f"<th>{_e(h)}</th>" for h in head)
    tr = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f'<table class="{cls}"><thead><tr>{th}</tr></thead><tbody>{tr}</tbody></table>'


def _lead(text: str) -> str:
    """Первое предложение — жирным: читатель видит суть, остальное — обоснование."""
    t = _e(text)
    m = re.search(r"\.\s(?=[А-ЯA-Z«])", t)
    return f"<b>{t[:m.start() + 1]}</b>{t[m.start() + 1:]}" if m else t


def _p(text: str, cls: str = "") -> str:
    return f"<p class='{cls}'>{_e(text)}</p>" if cls else f"<p>{_e(text)}</p>"


def build(res: Results) -> Report:
    f = Fmt()
    s, a, m = res.snap, res.analysis, res.analysis.metrics
    mk, gs, rv, ru, cmp = res.market, res.gspread, res.signals, res.rules, res.comparison
    D = f.date
    # Названия бумаг — данные файла (в них бывают цифры: «US T-bills 2026»).
    for lab in list(s.positions["label"]) + (list(res.prev.positions["label"]) if res.prev is not None else []):
        f.quote(lab)
    key_points, sections = [], []
    dq = res.quality
    errs = dq[dq["Уровень"] == "Ошибка"]
    wide = rv[rv["Сигнал"] == "шире группы"] if rv is not None else pd.DataFrame()
    narrow = rv[rv["Сигнал"] == "уже группы"] if rv is not None else pd.DataFrame()
    conc = a.concentration[a.concentration["Статус"] != "в пределах"]

    # ---------- Главное ----------
    if ru:
        for act in ru["actions"]:
            if act["Приоритет"] in (1, 2):
                key_points.append(_lead(N.action(act, f)))
    tgt = N.targets(a.targets, m, a.proforma, f)
    if tgt:
        key_points.append(_lead(tgt))
    dur_txt = dq[(dq["Категория"] == "Дашборд") & dq["Комментарий"].str.contains("Целевая дюрация")]
    if not dur_txt.empty:
        key_points.append(_lead(f.quote(dur_txt.iloc[0]["Комментарий"])))
    if gs is not None and gs.systematic["flag"]:
        wider = gs.systematic["median"] < 0
        txt = (f"G-spread в файле в среднем на {f.bp(abs(gs.systematic['median']))} "
               f"{'шире' if wider else 'уже'}, чем при пересчёте по кривой Treasury на {D(gs.curve.date)}: "
               f"средневзвешенный спред портфеля — {f.bp(gs.portfolio['file'])} в файле и "
               f"{f.bp(gs.portfolio['recomputed'])} по пересчёту. Расхождение одинаково по всем бумагам, поэтому "
               "это вопрос методики расчёта спреда в файле, а не ошибки по отдельным позициям.")
        imp = {k: v for k, v in gs.implied.items() if k in METHOD_LABEL}
        if imp:
            best_m = min(imp, key=lambda k: imp[k]["mae"])
            b = imp[best_m]
            txt += (f" Лучше всего спреды файла воспроизводятся кривой на {D(b['date'])} при интерполяции "
                    f"{METHOD_LABEL[best_m]} (среднее отклонение ±{f.num(b['mae'], 1)} б.п.), то есть, вероятно, "
                    "они посчитаны раньше даты сборки файла. Предлагаем уточнить у команды дату и кривую расчёта "
                    "спредов.")
        key_points.append(_lead(N.clean(txt)))
    if not wide.empty:
        key_points.append(_lead(
            "Несколько бумаг торгуются заметно шире пиров того же рейтинга и дюрации: "
            + "; ".join(f"{N.name(r['Позиция'])} ({r['Рейтинг']}) — на {f.bp(r['Премия, бп'])}"
                        for _, r in wide.iterrows())
            + ". Рынок закладывает в них риск сверх рейтинга, поэтому наращивать эти позиции не предлагаем; "
              "разбор по каждой бумаге — в разделе «Риски и сигналы»."))
    if not conc.empty:
        br = conc[conc["Статус"] == "превышен"]
        near = conc[conc["Статус"] != "превышен"]
        txt = ""
        if not br.empty:
            txt += "Превышены лимиты концентрации: " + "; ".join(
                f"{N.subject(r)} — {f.pct(r['Доля'], 1)} NAV при лимите {f.pct(r['Лимит'], 1)}"
                for _, r in br.iterrows()) + ". "
        if not near.empty:
            txt += "Близко к лимиту: " + "; ".join(
                f"{N.subject(r)} — {f.pct(r['Доля'], 1)} NAV при лимите {f.pct(r['Лимит'], 1)}"
                for _, r in near.iterrows()) + ". "
        txt += "Предложения — в разделе «Предложения по ребалансировке»."
        key_points.append(_lead(txt))
    if not errs.empty:
        key_points.append(_lead(
            f"В файле компании найдено ошибок: {f.int(len(errs))}, предупреждений: "
            f"{f.int((dq['Уровень'] == 'Предупреждение').sum())}. По видам ошибок: "
            + ", ".join(f"{k.lower()} — {f.int(v)}" for k, v in errs["Категория"].value_counts().items())
            + ". Они влияют на точность метрик, поэтому их стоит исправить в файле или внести поправками "
              "аналитика; описание каждой — в разделе «Риски и сигналы»."))
    if cmp is not None:
        key_points.insert(0, _lead(N.comparison(cmp, f)))

    # ---------- Портфель и цели ----------
    rows = []
    for _, r in a.targets.iterrows():
        is_y = r["key"].startswith("ytm")
        fmtv = (lambda x: f.pct(x)) if is_y else (lambda x: f.num(x)) if r["key"] == "mod_duration" else (lambda x: f.bp(x))
        gap = r["Отклонение"]
        gap_s = "—" if pd.isna(gap) else (f.bp_from(gap) if is_y else f.num(gap, sign=True) if r["key"] == "mod_duration"
                                         else f.bp(gap, sign=True))
        pf = r.get("Pro forma при целевой аллокации")
        rows.append([_e(r["Метрика"]), fmtv(r["Текущее"]), "—" if pd.isna(r["Цель"]) else fmtv(r["Цель"]), gap_s,
                     "—" if pf is None or pd.isna(pf) else fmtv(pf)])
    body = _table(["Метрика", "Текущее", "Цель", "Отклонение", "Pro forma*"], rows)
    body += _p(f"Средний рейтинг портфеля — {m['avg_rating']}. Доля бумаг инвестиционного уровня — "
               f"{f.pct(m['ig_share'], 1)}, HY — {f.pct(m['hy_share'], 1)}, денежных средств и T-bills — "
               f"{f.pct(m['cash_share'], 1)}, развивающихся рынков — {f.pct(m['em_share'], 1)}. В портфеле "
               f"{f.int(m['n_positions'])} позиций {f.int(m['n_issuers'])} эмитентов. *Pro forma — метрики при "
               "целевых долях сегментов и нынешнем составе бумаг внутри них.", "small")
    if a.allocation is not None:
        al_rows = [[_e(r["Sleeve"]), f.pct(r["Текущее"], 1), f.pct(r["Цель"], 1), f.pp(r["Отклонение"]), _e(r["Статус"])]
                   for _, r in a.allocation.iterrows()]
        body += _table(["Сегмент", "Текущая доля", "Цель", "Отклонение", "Статус"], al_rows)
        al = N.allocation(a.allocation, f)
        if al:
            body += _p(al)
    sections.append(("Портфель и цели", body))

    # ---------- Изменения к прошлому портфелю ----------
    if cmp is not None:
        mt = cmp.metrics
        kinds = {"pct": lambda x: f.bp_from(x), "share": lambda x: f.pp(x), "num": lambda x: f.num(x, sign=True),
                 "bp": lambda x: f.bp(x, sign=True, d=1), "int": lambda x: f.num(x, 0, sign=True),
                 "txt": lambda x: f.num(x, sign=True) + " ступени"}
        ch_rows = [[_e(r["Метрика"]), kinds[r["Тип"]](r["Изменение"])] for _, r in mt.iterrows()
                   if r["Изменение"] is not None and not pd.isna(r["Изменение"]) and r["key"] in
                   ("ytm_gross", "mod_duration", "gspread", "dts", "avg_rating", "hy_share", "cash_share", "em_share")]
        at_rows = [[_e(r["Компонент"]), f.bp(r["Вклад"], sign=True, d=1)] for _, r in cmp.attribution["YTM, бп"].iterrows()]
        body = "<div class='two'>" + _table(["Метрика", "Изменение"], ch_rows) + \
               _table(["Вклад в изменение YTM", "б.п."], at_rows) + "</div>"
        pos = cmp.positions
        items = []
        new = [N.name(x) for x in pos[pos["Статус"] == "новая"]["Позиция"]]
        rem = [N.name(x) for x in pos[pos["Статус"] == "выбыла"]["Позиция"]]
        if new:
            items.append("В портфель добавлены: " + ", ".join(new) + ".")
        if rem:
            items.append("Из портфеля выбыли: " + ", ".join(rem) + ".")
        for _, r in cmp.rating_changes.iterrows():
            down = "IG в HY" in str(r["Изменения"])
            items.append(f"Рейтинг {N.name(r['Позиция'])} изменился с {r['Рейтинг ранее']} на {r['Рейтинг сейчас']}"
                         + ("; это переход из IG в HY, поэтому позицию нужно проверить на соответствие лимитам "
                            "и назначению сегмента." if down else "."))
        if res.spread_changes is not None and not res.spread_changes.empty:
            for _, r in res.spread_changes[res.spread_changes["Сигнал"] == "аномальное расширение"].iterrows():
                items.append(N.widening(r, f))
        if items:
            body += "<ul>" + "".join(f"<li>{_e(x)}</li>" for x in items) + "</ul>"
        for n in res.notes + cmp.notes:
            body += _p(f.quote(n), "small")
        sections.append((f"Изменения с {D(cmp.prev_date)}", body))

    # ---------- Рыночный фон и правила ----------
    if mk is not None:
        mrows = []
        y10, y2 = f.label("10Y UST"), f.label("2Y UST")
        last = mk.curve_latest
        if mk.curve_asof:
            for lbl, tnr in ((y10, 10), (y2, 2)):
                mrows.append([lbl, f"{f.pct(mk.curve_asof.at(tnr))} ({D(mk.curve_asof.date)})",
                              f"{f.pct(last.at(tnr))} ({D(last.date)})" if last else "—",
                              f.bp_from(last.at(tnr) - mk.curve_asof.at(tnr)) if last else "—", "U.S. Treasury"])
        hy_ok = mk.hy_asof and mk.hy_asof[0] and mk.hy_latest and mk.hy_latest[0]
        if hy_ok:
            mrows.append(["HY OAS", f"{f.bp(mk.hy_asof[1])} ({D(mk.hy_asof[0])})",
                          f"{f.bp(mk.hy_latest[1])} ({D(mk.hy_latest[0])})",
                          f.bp(mk.hy_latest[1] - mk.hy_asof[1], sign=True), "FRED (ICE BofA)"])
        body = ""
        mtxt = N.market(mk, f)
        if mtxt:
            body += _p(mtxt)
        body += _table(["Показатель", "На дату портфеля", "Последнее значение", "Изменение", "Источник"], mrows)
        if mk.errors:
            body += "".join(_p(f"Нет данных: {e}", "small warn") for e in mk.errors)
        if ru:
            rr = [[_e(f.quote(r["Правило"])), _e(f.quote(r["Сейчас"])), _e(f.quote(r["Касание с даты портфеля"])),
                   _e(r["Статус"])] for _, r in ru["table"].iterrows()]
            body += _table(["Правило (из файла)", "Сейчас", "Касание с даты портфеля", "Статус"], rr)
        sections.append(("Рыночный фон и правила добора", body))

    # ---------- Оценка на текущую дату и стресс-тест ----------
    est, stp = res.estimate, res.stress
    body = ""
    if est is not None:
        pe = est.portfolio
        body += _p(N.estimate(est, f))
        worst = est.positions.nsmallest(3, "Итого доходность")
        body += _p("Наибольший оценочный убыток — по позициям " + "; ".join(
            f"{N.name(r['Позиция'])} ({f.pct(r['Итого доходность'], 1, sign=True)}, OAS индекса "
            f"{f.bp(r['Δ OAS индекса, бп'], sign=True)})" for _, r in worst.iterrows()) + ".", "small")
        key_points.append(_lead(
            f"Оценка на {D(est.est_date)} по индексам: доходность портфеля около {f.pct(pe['ytm_est'])}, G-spread — "
            f"около {f.bp(pe['gs_est'])}. Результат с даты портфеля — примерно {f.pct(pe['total'], sign=True)} NAV "
            f"(цены {f.pct(pe['price'], sign=True)}, купон {f.pct(pe['carry'], sign=True)})."))
    if stp is not None:
        srows = [[_e(f.quote(r["Сценарий"])), f.pct(r["P&L портфеля, % NAV"], sign=True), _e(f.quote(r["Худшая позиция"])),
                  f.pct(r["P&L худшей позиции, % NAV"], sign=True)] for _, r in stp["summary"].iterrows()]
        body += _p(N.stress(stp, f))
        body += _table(["Стресс-сценарий (мгновенный шок)", "P&L, % NAV", "Худшая позиция", "P&L позиции, % NAV"], srows)
        body += _p("P&L позиции ≈ −модифицированная дюрация × (изменение UST + изменение спреда). Для бумаг, "
                   "относящихся одновременно к EM и HY, берётся больший из шоков; денежные позиции и T-bills "
                   "реагируют только на ставки.", "small")
    if body:
        title_e = f"Оценка на {D(est.est_date)} и стресс-тест" if est is not None else "Стресс-тест"
        sections.append((title_e, body))

    # ---------- Риски и сигналы ----------
    items = [N.concentration(r, f) for _, r in conc.iterrows()]
    items.append(f"Кредитный риск портфеля по DTS (модифицированная дюрация × спред) — {f.num(m['dts'], 0)}. Доля HY "
                 f"составляет {f.pct(m['hy_share'], 1)}, развивающихся рынков — {f.pct(m['em_share'], 1)}.")
    items += [N.wide(r, f) for _, r in wide.iterrows()]
    items += [N.narrow(r, f) for _, r in narrow.iterrows()]
    for _, r in errs.iterrows():
        pos = "" if r["Позиция"] == "Портфель" else f"{N.name(r['Позиция'])}: "
        items.append(f"Качество данных. {pos}{f.quote(r['Комментарий'])}")
    sections.append(("Риски и сигналы", "<ul>" + "".join(f"<li>{_lead(x)}</li>" for x in items) + "</ul>"))

    # ---------- Предложения по ребалансировке ----------
    items = []
    if ru:
        items += [N.action(act, f) for act in ru["actions"]]
    for _, r in conc[conc["Статус"] == "превышен"].iterrows():
        items.append(f"Предлагаем сократить долю {N.subject(r, gen=True)} с "
                     f"{f.pct(r['Доля'], 1)} до лимита {f.pct(r['Лимит'], 1)} NAV или согласовать исключение с "
                     "командой: превышение лимита усиливает зависимость портфеля от одного источника риска.")
    if not wide.empty:
        items.append("Перед доборами по стратегии предлагаем проверить бумаги со спредом шире пиров — "
                     + ", ".join(N.name(x) for x in wide["Позиция"]) + ". Если премия связана с ухудшением "
                     "кредитного качества, эти позиции не стоит наращивать, а при поиске финансирования для доборов "
                     "их можно рассмотреть среди кандидатов на сокращение.")
    sections.append(("Предложения по ребалансировке", "<ol>" + "".join(f"<li>{_lead(N.clean(x))}</li>" for x in items)
                     + "</ol>" + _p("Предложения следуют из правил стратегии компании, отклонений от целевой "
                                    "аллокации и сигналов по спредам. Решение по каждой сделке остаётся за "
                                    "аналитиком.", "small")))

    # ---------- Поправки аналитика ----------
    cl = res.corrections
    if cl is not None and not cl.empty:
        ok = cl[cl["Статус"] == "применена"]
        rows = [[_e(f.quote(r["Позиция"])), _e(r["Поле"]), _e(f.quote(r["Было"])), _e(f.quote(r["Стало"])),
                 _e(f.quote(r["Основание"])), _e(f.quote(r["Статус"]))] for _, r in cl.iterrows()]
        sections.append(("Поправки аналитика", _table(
            ["Позиция", "Поле", "Было", "Стало", "Основание", "Статус"], rows, cls="corr")
            + _p("Исходный файл компании не изменён: поправки применены поверх него, и все цифры записки посчитаны "
                 "по исправленным данным. Поправка применяется, только если значение в файле по-прежнему равно "
                 "«было», поэтому в следующем файле она не перезапишет исправленные командой данные.", "small")))
        if not ok.empty:
            key_points.insert(0, _lead(
                f"В расчёт внесены поправки аналитика ({f.int(len(ok))}): " + "; ".join(
                    f"{N.name(f.quote(r['Позиция']))} — {r['Поле'].lower()} {f.quote(r['Было'])} → "
                    f"{f.quote(r['Стало'])}" for _, r in ok.iterrows())
                + ". Исходный файл не изменён, все цифры записки посчитаны по исправленным данным."))

    # ---------- Оговорки ----------
    src = [f"Данные по бумагам взяты из файла компании «{f.quote(s.source_name)}» на {D(s.as_of)}."]
    if res.prev is not None:
        src.append(f"Прошлый портфель — файл «{f.quote(res.prev.source_name)}» на {D(res.prev.as_of)}.")
    src.append(f"Кривая UST — {SOURCE_UST}; спред HY OAS — {SOURCE_HY}.")
    src.append("Бесплатных официальных котировок по отдельным еврооблигациям нет, поэтому доходности и спреды бумаг "
               "взяты из файла компании, а производные метрики посчитаны по формулам из методологии.")
    src.append("Пороги концентрации и сигналов — допущения инструмента; их значения и обоснование приведены в "
               "методологии.")
    sections.append(("Источники и оговорки", "<ul class='small'>" + "".join(f"<li>{_e(x)}</li>" for x in src) + "</ul>"))

    title = f"Портфель еврооблигаций — аналитическая записка на {D(s.as_of)}"
    sub = (f"Сравнение с {D(res.prev.as_of)} · " if res.prev is not None else "") + \
          f"Рыночные данные на {D(mk.curve_latest.date) if mk and mk.curve_latest else '—'} · " \
          f"Подготовлено {D(res.today)} · Версия текста: {TEXT_VERSION}"
    body = (f"<h1>{_e(title)}</h1><p class='sub'>{_e(sub)}</p>"
            "<h2>Главное</h2><ul class='key'>" + "".join(f"<li>{x}</li>" for x in key_points) + "</ul>"
            + "".join(f"<h2>{_e(h)}</h2>{b}" for h, b in sections)
            + "<p class='disclaimer'>Решение остаётся за аналитиком. Записка подготовлена автоматически по файлу "
              "компании и данным Treasury и FRED и не является инвестиционной рекомендацией.</p>")
    body = N.clean(body)
    doc = HTML_TEMPLATE.replace("{{title}}", _e(title)).replace("{{body}}", body)
    text = re.sub(r"<[^>]+>", " ", body)
    text = html.unescape(re.sub(r"\s+", " ", text))
    return Report(title=title, html=doc, text=text, numbers=f.numbers, quotes=f.quotes)


HTML_TEMPLATE = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{title}}</title>
<style>
  :root { --ink:#1a1a19; --muted:#5e5d59; --line:#dddcd5; --accent:#2a78d6; --bg:#ffffff; --soft:#f6f6f3; }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--ink);
         font: 10pt/1.45 system-ui, -apple-system, "Segoe UI", Roboto, Arial, sans-serif; }
  main { max-width: 190mm; margin: 0 auto; padding: 12mm 20px; }
  h1 { font-size: 15pt; margin: 0 0 2pt; }
  h2 { font-size: 11pt; margin: 12pt 0 4pt; padding-bottom: 2pt; border-bottom: 1.5px solid var(--accent); }
  p { margin: 4pt 0; }
  .sub { color: var(--muted); margin: 0 0 6pt; font-size: 8.5pt; }
  ul, ol { margin: 4pt 0; padding-left: 16pt; } li { margin: 3pt 0; }
  ul.key li { margin: 4pt 0; }
  table { border-collapse: collapse; width: 100%; margin: 4pt 0; font-size: 9pt; font-variant-numeric: tabular-nums; }
  th, td { border-bottom: 1px solid var(--line); padding: 2.5pt 5pt; text-align: left; vertical-align: top; }
  th { background: var(--soft); font-weight: 600; }
  td:not(:first-child) { white-space: nowrap; }
  .two { display: grid; grid-template-columns: 1fr 1fr; gap: 10pt; }
  .small { font-size: 8.5pt; color: var(--muted); }
  .warn { color: #b3261e; }
  .disclaimer { margin-top: 12pt; padding: 6pt 8pt; background: var(--soft); border-left: 3px solid var(--accent);
                font-weight: 600; }
  .print { position: fixed; top: 10px; right: 10px; padding: 6px 12px; border: 1px solid var(--line);
           background: #fff; border-radius: 6px; cursor: pointer; font: inherit; }
  @media (max-width: 700px) { main { padding: 16px; } .two { grid-template-columns: 1fr; } td { white-space: normal; } }
  @media print { .print { display: none; } main { padding: 0; } @page { size: A4; margin: 12mm; }
                 h2 { break-after: avoid; } table, li { break-inside: avoid; } }
</style></head>
<body><button class="print" onclick="window.print()">Печать / PDF</button><main>{{body}}</main></body></html>
"""
