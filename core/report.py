"""Отчёт на 1–2 страницы (HTML, печать в PDF из браузера).

Текст собирается по шаблонам из рассчитанных значений. Каждое число проходит через форматтер,
который записывает его в реестр (`Report.numbers`): тест проверяет, что в отчёте нет чисел вне реестра
(кроме дат и цитат из файла компании). Версия текста: «шаблон» (LLM не используется).
"""
from __future__ import annotations

import datetime as dt
import html
import re
from dataclasses import dataclass, field

import pandas as pd

from .gspread import METHOD_LABEL
from .market import SOURCE_HY, SOURCE_UST
from .pipeline import Results

TEXT_VERSION = "шаблон"


class Fmt:
    """Форматирование чисел в русском деловом стиле (десятичная запятая) с реестром всех выведенных чисел."""

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

    def pp(self, x, d=1, sign=True):
        return self.num(x * 100, d, sign) + " п.п."

    def bp(self, x, sign=False, d=0):
        return self.num(x, d, sign) + " бп"

    def bp_from(self, x, sign=True, d=0):
        return self.bp(x * 10000, sign, d)

    def int(self, x):
        return self._reg(f"{int(round(x))}")

    @staticmethod
    def date(d):
        return d.strftime("%d.%m.%Y") if d else "—"

    def quote(self, text: str) -> str:
        """Готовый текст из расчётных модулей или цитата из файла: десятичная точка -> запятая (даты не трогаются)."""
        text = re.sub(r"(?<![\d.])(\d+)\.(\d+)(?![\d.])", r"\1,\2", str(text))
        self.quotes.append(text)
        return text


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


def build(res: Results) -> Report:
    f = Fmt()
    s, a, m = res.snap, res.analysis, res.analysis.metrics
    mk, gs, rv, ru, cmp = res.market, res.gspread, res.signals, res.rules, res.comparison
    D = f.date
    key_points, sections = [], []

    # ---------- Главное ----------
    if ru:
        for act in ru["actions"]:
            if act["Приоритет"] == 1:
                key_points.append(f"<b>Сработало правило стратегии.</b> {_e(f.quote(act['Основание']))} → "
                                  f"{_e(f.quote(act['Действие']))}. Финансирование: {_e(f.quote(act['Финансирование']))}.")
            elif act["Приоритет"] == 2:
                key_points.append(f"<b>Проверить исполнение.</b> {_e(f.quote(act['Действие']))} "
                                  f"Основание: {_e(f.quote(act['Основание']))}.")
    t = a.targets.set_index("key")
    tg = t.loc["ytm_gross"]
    if pd.notna(tg["Цель"]):
        pf = a.proforma
        txt = (f"<b>Доходность ниже цели.</b> YTM брутто {f.pct(m['ytm_gross'])} (после комиссии "
               f"{f.pct(m['ytm_net'])}) против цели {f.pct(tg['Цель'])} ({f.bp_from(tg['Отклонение'])}); "
               f"mod. duration {f.num(m['mod_duration'])} против {f.num(t.loc['mod_duration', 'Цель'])}.")
        if pf:
            txt += (f" При целевой аллокации (pro forma): YTM {f.pct(pf['ytm_gross'])}, дюрация "
                    f"{f.num(pf['mod_duration'])}, G-spread {f.bp(pf['gspread'])}.")
        if tg["Отклонение"] >= 0:
            txt = txt.replace("Доходность ниже цели.", "Доходность на уровне цели или выше.")
        key_points.append(txt)
    dq = res.quality
    dur_txt = dq[(dq["Категория"] == "Дашборд") & dq["Комментарий"].str.contains("Целевая mod. duration")]
    if not dur_txt.empty:
        key_points.append(f"<b>Цель по дюрации требует уточнения:</b> {_e(f.quote(dur_txt.iloc[0]['Комментарий']))}")
    if gs is not None and gs.systematic["flag"]:
        txt = (f"<b>G-spread в файле систематически отличается от пересчёта</b> по кривой Treasury на "
               f"{D(gs.curve.date)}: медиана {f.bp(gs.systematic['median'], sign=True)} по бумагам; средний "
               f"G-spread портфеля {f.bp(gs.portfolio['file'])} в файле против {f.bp(gs.portfolio['recomputed'])}.")
        imp = {k: v for k, v in gs.implied.items() if k in METHOD_LABEL}
        if imp:
            best_m = min(imp, key=lambda k: imp[k]["mae"])
            b = imp[best_m]
            txt += (f" Гипотеза: спреды в файле рассчитаны по кривой на {D(b['date'])} ({METHOD_LABEL[best_m]}; "
                    f"средний модуль расхождения {f.num(b['mae'], 1)} бп) — уточнить у команды.")
        key_points.append(txt)
    wide = rv[rv["Сигнал"] == "шире группы"] if rv is not None else pd.DataFrame()
    if not wide.empty:
        key_points.append("<b>Рынок закладывает риск сверх рейтинга:</b> " + "; ".join(
            f"{_e(r['Позиция'])} ({_e(r['Рейтинг'])}) {f.bp(r['Премия, бп'], sign=True)} к пирам"
            for _, r in wide.iterrows()) + ".")
    conc = a.concentration[a.concentration["Статус"] != "в пределах"]
    if not conc.empty:
        key_points.append("<b>Концентрации:</b> " + "; ".join(
            f"{_e(r['Тип']).lower()} {_e(r['Объект'])} — {f.pct(r['Доля'], 1)} при лимите {f.pct(r['Лимит'], 1)} "
            f"({_e(r['Статус'])})" for _, r in conc.iterrows()) + ".")
    errs = dq[dq["Уровень"] == "Ошибка"]
    if not errs.empty:
        key_points.append(f"<b>Качество данных:</b> ошибок {f.int(len(errs))}, предупреждений "
                          f"{f.int((dq['Уровень'] == 'Предупреждение').sum())} — " + "; ".join(
                              f"{_e(r['Позиция'])}: {_e(r['Категория']).lower()}" for _, r in errs.iterrows()) + ".")
    if cmp is not None:
        mt = cmp.metrics.set_index("key")
        at = cmp.attribution["YTM, бп"]
        comp = at[at["Компонент"] != "Итого изменение портфеля"]
        main = comp.loc[comp["Вклад"].abs().idxmax()]
        key_points.insert(0, f"<b>С {D(cmp.prev_date)}:</b> YTM портфеля {f.bp_from(mt.loc['ytm_gross', 'Изменение'])}, "
                             f"G-spread {f.bp(mt.loc['gspread', 'Изменение'], sign=True, d=1)}, дюрация "
                             f"{f.num(mt.loc['mod_duration', 'Изменение'], sign=True)}; наибольший вклад в изменение "
                             f"доходности — «{_e(main['Компонент'])}» ({f.bp(main['Вклад'], sign=True, d=1)}).")

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
    port = _table(["Метрика", "Текущее", "Цель", "Отклонение", "Pro forma*"], rows)
    extra = (f"<p class='small'>Средний рейтинг {_e(m['avg_rating'])}; IG {f.pct(m['ig_share'], 1)}, HY "
             f"{f.pct(m['hy_share'], 1)}, кэш {f.pct(m['cash_share'], 1)}, EM {f.pct(m['em_share'], 1)}; позиций "
             f"{f.int(m['n_positions'])}, эмитентов {f.int(m['n_issuers'])}. *Pro forma — при целевых долях sleeve "
             f"и неизменном составе бумаг внутри sleeve.</p>")
    if a.allocation is not None:
        al_rows = [[_e(r["Sleeve"]), f.pct(r["Текущее"], 1), f.pct(r["Цель"], 1), f.pp(r["Отклонение"]), _e(r["Статус"])]
                   for _, r in a.allocation.iterrows()]
        extra += _table(["Sleeve", "Текущая", "Цель", "Отклонение", "Статус"], al_rows)
    sections.append(("Портфель и цели", port + extra))

    # ---------- Изменения с прошлого снимка ----------
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
               _table(["Вклад в изменение YTM", "бп"], at_rows) + "</div>"
        pos = cmp.positions
        items = []
        new = pos[pos["Статус"] == "новая"]["Позиция"].tolist()
        rem = pos[pos["Статус"] == "выбыла"]["Позиция"].tolist()
        if new:
            items.append("Новые: " + ", ".join(_e(x) for x in new))
        if rem:
            items.append("Выбыли: " + ", ".join(_e(x) for x in rem))
        for _, r in cmp.rating_changes.iterrows():
            items.append(f"Рейтинг {_e(r['Позиция'])}: {_e(r['Рейтинг ранее'])} → {_e(r['Рейтинг сейчас'])}"
                         + (" (переход из IG в HY)" if "IG в HY" in str(r["Изменения"]) else ""))
        if res.spread_changes is not None and not res.spread_changes.empty:
            for _, r in res.spread_changes[res.spread_changes["Сигнал"] == "аномальное расширение"].iterrows():
                items.append(f"Аномальное расширение спреда: {_e(r['Позиция'])} {f.bp(r['Изменение, бп'], sign=True)} "
                             f"при медиане группы {f.bp(r['Медианное изменение группы, бп'], sign=True)}")
        if items:
            body += "<ul>" + "".join(f"<li>{x}</li>" for x in items) + "</ul>"
        for n in res.notes + cmp.notes:
            body += f"<p class='small'>{_e(f.quote(n))}</p>"
        sections.append((f"Изменения с {D(cmp.prev_date)}", body))

    # ---------- Рыночный фон и правила ----------
    if mk is not None:
        mrows = []
        if mk.curve_asof:
            last = mk.curve_latest
            mrows.append(["10Y UST", f"{f.pct(mk.curve_asof.at(10))} ({D(mk.curve_asof.date)})",
                          f"{f.pct(last.at(10))} ({D(last.date)})" if last else "—",
                          f.bp_from(last.at(10) - mk.curve_asof.at(10)) if last else "—", "U.S. Treasury"])
            mrows.append(["2Y UST", f"{f.pct(mk.curve_asof.at(2))} ({D(mk.curve_asof.date)})",
                          f"{f.pct(last.at(2))} ({D(last.date)})" if last else "—",
                          f.bp_from(last.at(2) - mk.curve_asof.at(2)) if last else "—", "U.S. Treasury"])
        if mk.hy_asof and mk.hy_asof[0]:
            mrows.append(["HY OAS", f"{f.bp(mk.hy_asof[1])} ({D(mk.hy_asof[0])})",
                          f"{f.bp(mk.hy_latest[1])} ({D(mk.hy_latest[0])})",
                          f.bp(mk.hy_latest[1] - mk.hy_asof[1], sign=True), "FRED (ICE BofA)"])
        body = _table(["Показатель", "На дату снимка", "Последнее", "Изменение", "Источник"], mrows)
        if mk.errors:
            body += "".join(f"<p class='small warn'>Нет данных: {_e(e)}</p>" for e in mk.errors)
        if ru:
            rr = [[_e(f.quote(r["Правило"])), _e(f.quote(r["Сейчас"])), _e(f.quote(r["Касание с даты снимка"])),
                   _e(r["Статус"])] for _, r in ru["table"].iterrows()]
            body += _table(["Правило (из файла)", "Сейчас", "Касание с даты снимка", "Статус"], rr)
        sections.append(("Рыночный фон и правила добора", body))

    # ---------- Оценка на текущую дату и стресс-тест ----------
    est, stp = res.estimate, res.stress
    body = ""
    if est is not None:
        pe = est.portfolio
        body += (f"<p>Оценка по индексам ICE BofA (FRED) и кривой Treasury с {D(est.as_of)} по {D(est.est_date)} "
                 f"({f.int(pe['days'])} дн.): YTM портфеля ≈ {f.pct(pe['ytm_est'])} ({f.bp_from(pe['ytm_est'] - pe['ytm_file'])}), "
                 f"G-spread ≈ {f.bp(pe['gs_est'])} ({f.bp(pe['gs_est'] - pe['gs_file'], sign=True)}); ценовой эффект "
                 f"≈ {f.pct(pe['price'], sign=True)}, купонный доход ≈ {f.pct(pe['carry'], sign=True)}, итого ≈ "
                 f"{f.pct(pe['total'], sign=True)} NAV. <i>Это оценка, а не данные по бумагам.</i></p>")
        worst = est.positions.nsmallest(3, "Итого доходность")
        body += "<p class='small'>Сильнее всего (оценка): " + "; ".join(
            f"{_e(f.quote(r['Позиция']))} {f.pct(r['Итого доходность'], 1, sign=True)} (OAS индекса "
            f"{f.bp(r['Δ OAS индекса, бп'], sign=True)})" for _, r in worst.iterrows()) + ".</p>"
        key_points.append(f"<b>Оценка на {D(est.est_date)} (по индексам FRED):</b> YTM портфеля ≈ {f.pct(pe['ytm_est'])}, "
                          f"G-spread ≈ {f.bp(pe['gs_est'])}; доходность с даты снимка ≈ {f.pct(pe['total'], sign=True)} NAV "
                          f"(цена {f.pct(pe['price'], sign=True)}, купон {f.pct(pe['carry'], sign=True)}).")
    if stp is not None:
        srows = [[_e(f.quote(r["Сценарий"])), f.pct(r["P&L портфеля, % NAV"], sign=True), _e(f.quote(r["Худшая позиция"])),
                  f.pct(r["P&L худшей позиции, % NAV"], sign=True)] for _, r in stp["summary"].iterrows()]
        body += _table(["Стресс-сценарий (мгновенный шок)", "P&L, % NAV", "Наибольший вклад", "его P&L, % NAV"], srows)
        body += "<p class='small'>P&L ≈ −mod. duration × (ΔUST + Δспреда); EM и HY — без двойного счёта; кэш — только UST.</p>"
    if body:
        title_e = f"Оценка на {D(est.est_date)} и стресс-тест" if est is not None else "Стресс-тест"
        sections.append((title_e, body))

    # ---------- Риски и сигналы ----------
    items = []
    for _, r in conc.iterrows():
        items.append(f"{_e(r['Тип'])}: {_e(r['Объект'])} — {f.pct(r['Доля'], 1)} при лимите {f.pct(r['Лимит'], 1)} "
                     f"({_e(r['Статус'])}; основание: {_e(f.quote(r['Основание лимита']))})")
    items.append(f"Кредитный риск (DTS): {f.num(m['dts'], 0)}; доля HY {f.pct(m['hy_share'], 1)}, доля EM {f.pct(m['em_share'], 1)}.")
    for _, r in wide.iterrows():
        rel = "" if r["Надёжность"] == "достаточно пиров" else f" ({_e(r['Надёжность'])})"
        items.append(f"{_e(r['Позиция'])} ({_e(r['Рейтинг'])}, {_e(r['Регион'])}): G-spread {f.bp(r['G-spread'])} против "
                     f"медианы пиров {f.bp(r['Медиана пиров'])}{rel}")
    for _, r in errs.iterrows():
        items.append(f"Данные — {_e(r['Позиция'])}: {_e(f.quote(r['Комментарий']))}")
    sections.append(("Риски и сигналы", "<ul>" + "".join(f"<li>{x}</li>" for x in items) + "</ul>"))

    # ---------- Предложения по ребалансировке ----------
    items = []
    if ru:
        for act in ru["actions"]:
            items.append(f"<b>{_e(f.quote(act['Действие']))}</b> — {_e(f.quote(act['Основание']))}. "
                         f"Финансирование: {_e(f.quote(act['Финансирование']))}.")
    if a.allocation is not None:
        off = a.allocation[a.allocation["Статус"] != "в пределах"]
        if not off.empty:
            items.append("Отклонения от целевой аллокации (путь к цели задают правила выше): " + "; ".join(
                f"{_e(r['Sleeve'])} {f.pp(r['Отклонение'])}" for _, r in off.iterrows()))
    if not wide.empty:
        items.append("Проверить бумаги со спредом шире пиров (новости эмитента, ликвидность, структура выпуска) "
                     "перед доборами в их сегменты: " + ", ".join(_e(x) for x in wide["Позиция"]) + ".")
    sec_breach = conc[(conc["Статус"] == "превышен")]
    for _, r in sec_breach.iterrows():
        items.append(f"Рассмотреть снижение концентрации: {_e(r['Тип']).lower()} {_e(r['Объект'])} "
                     f"{f.pct(r['Доля'], 1)} при лимите {f.pct(r['Лимит'], 1)}.")
    sections.append(("Предложения по ребалансировке", "<ol>" + "".join(f"<li>{x}</li>" for x in items) + "</ol>"
                     "<p class='small'>Предложения сформированы программой по правилам стратегии компании, отклонениям "
                     "от целевой аллокации и сигналам; это не рекомендация к сделке.</p>"))

    # ---------- Оговорки ----------
    src = [f"Данные по бумагам — файл компании «{_e(f.quote(s.source_name))}» (снимок {D(s.as_of)})."]
    if res.prev is not None:
        src.append(f"Предыдущий снимок — «{_e(f.quote(res.prev.source_name))}» ({D(res.prev.as_of)}).")
    src.append(f"Кривая UST — {_e(SOURCE_UST)}; HY OAS — {_e(SOURCE_HY)}.")
    src.append("Бесплатных официальных источников цен по отдельным еврооблигациям нет: доходности и спреды бумаг "
               "взяты из файла компании; производные метрики — расчётом (формулы — в разделе «Методология»).")
    src.append("Пороги концентрации и сигналов — допущения аналитика (config.yaml), значения указаны в методологии.")
    sections.append(("Источники и оговорки", "<ul class='small'>" + "".join(f"<li>{x}</li>" for x in src) + "</ul>"))

    title = f"Портфель еврооблигаций — аналитическая записка на {D(s.as_of)}"
    sub = (f"Сравнение с {D(res.prev.as_of)} · " if res.prev is not None else "") + \
          f"Рыночные данные на {D(mk.curve_latest.date) if mk and mk.curve_latest else '—'} · " \
          f"Сформировано {D(res.today)} · Версия текста: {TEXT_VERSION}"
    body = (f"<h1>{_e(title)}</h1><p class='sub'>{_e(sub)}</p>"
            "<h2>Главное</h2><ul class='key'>" + "".join(f"<li>{x}</li>" for x in key_points) + "</ul>"
            + "".join(f"<h2>{_e(h)}</h2>{b}" for h, b in sections)
            + "<p class='disclaimer'>Решение остаётся за аналитиком. Отчёт сформирован автоматически по данным файла "
              "компании и официальных источников; он не является инвестиционной рекомендацией.</p>")
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
         font: 10pt/1.42 system-ui, -apple-system, "Segoe UI", Roboto, Arial, sans-serif; }
  main { max-width: 190mm; margin: 0 auto; padding: 12mm 20px; }
  h1 { font-size: 15pt; margin: 0 0 2pt; }
  h2 { font-size: 11pt; margin: 12pt 0 4pt; padding-bottom: 2pt; border-bottom: 1.5px solid var(--accent); }
  .sub { color: var(--muted); margin: 0 0 6pt; font-size: 8.5pt; }
  ul, ol { margin: 4pt 0; padding-left: 16pt; } li { margin: 2pt 0; }
  ul.key li { margin: 3pt 0; }
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
