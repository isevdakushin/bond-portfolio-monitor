"""Текст выводов полными предложениями: что произошло, почему это важно и что предлагаем сделать.

Одни и те же формулировки — во вкладках интерфейса и в отчёте. Все числа проходят через форматтер
(`core.fmt.Fmt`), поэтому в отчёте их можно сверить с расчётом. Интерпретации следуют из данных
(например, BBB- — последняя ступень инвестиционного уровня); фактов, которых нет в данных, текст не добавляет.
"""
from __future__ import annotations

import re

import pandas as pd

from . import ratings
from .fmt import Fmt

ORDINAL = {1: "первый", 2: "второй", 3: "третий", 4: "четвёртый", 5: "пятый"}
TIMES = {1: "один раз", 2: "дважды", 3: "трижды"}
MONTH_IN = {1: "январе", 2: "феврале", 3: "марте", 4: "апреле", 5: "мае", 6: "июне", 7: "июле", 8: "августе",
            9: "сентябре", 10: "октябре", 11: "ноябре", 12: "декабре"}
GEN = {"Эмитент": "эмитента", "Страна": "страны", "Сектор (укрупнённо)": "сектора", "Регион": "региона",
       "Кредитное качество": "кредитного сегмента"}


def clean(text: str) -> str:
    """Точка после сокращения в конце предложения: «127 б.п..» -> «127 б.п.»."""
    return re.sub(r"(б\.п|п\.п)\.\.", r"\1.", text)


def name(label) -> str:
    """«#13 Adani Ports & SEZ» -> «Adani Ports & SEZ»."""
    return re.sub(r"^#\d+\s+", "", str(label))


def _peers_word(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return "сопоставимая бумага"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "сопоставимые бумаги"
    return "сопоставимых бумаг"


def _join(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " и " + items[-1]


def _reliability(r, f: Fmt) -> str:
    rel = str(r.get("Надёжность", "") or "")
    out = []
    if "мало пиров" in rel:
        n = int(r["Пиров"])
        out.append(f"Вывод предварительный: в портфеле всего {f.int(n)} {_peers_word(n)} того же рейтинга, поэтому "
                   "медиана неустойчива и её стоит подтвердить рыночными данными вне портфеля.")
    if "окно" in rel and pd.notna(r.get("Окно дюрации, ±лет")):
        out.append(f"Для поиска пиров окно по дюрации расширено до ±{f.num(r['Окно дюрации, ±лет'], 0)} лет.")
    return (" " + " ".join(out)) if out else ""


def _head(r, f: Fmt) -> str:
    return (f"{name(r['Позиция'])} ({r['Рейтинг']}, {r['Регион']}, модифицированная дюрация "
            f"{f.num(r['Mod. duration'], 1)})")


# ---------- сигналы по спредам ----------

def wide(r, f: Fmt | None = None) -> str:
    """Бумага шире пиров того же рейтинга."""
    f = f or Fmt()
    rt = ratings.normalize(r["Рейтинг"])
    ig = ratings.is_ig(rt)
    txt = (f"{_head(r, f)} торгуется со спредом {f.bp(r['G-spread'])} к UST — это на {f.bp(r['Премия, бп'])}, "
           f"или примерно на {f.num(r['Премия, %'] * 100, 0)}%, шире медианы бумаг с похожим рейтингом и дюрацией "
           f"({f.bp(r['Медиана пиров'])}). Такая премия говорит о том, что рынок оценивает риск эмитента выше, "
           "чем предполагает рейтинг")
    if rt == ratings.IG_FLOOR:
        txt += (f", а рейтинг {rt} находится на нижней границе инвестиционного уровня. Предлагаем не наращивать "
                "позицию и отслеживать рейтинговые действия: понижение на одну ступень переведёт бумагу в "
                "категорию HY.")
    elif ig:
        txt += (". Предлагаем не наращивать позицию, пока не ясна причина премии: проверить новости эмитента, "
                "ликвидность выпуска и его структуру (субординацию, колл-опционы). Если причина техническая, а "
                "кредитное качество не ухудшилось, премия может быть возможностью для добора.")
    else:
        txt += (". Для бумаги категории HY это может отражать риск дальнейшего понижения рейтинга или трудности с "
                "рефинансированием. Предлагаем не наращивать позицию и проверить новости эмитента и ликвидность "
                "выпуска.")
    if str(r.get("Регион", "")).upper() == "EM":
        txt += " Эмитент относится к развивающимся рынкам, поэтому часть премии может приходиться на страновой риск."
    return clean(txt + _reliability(r, f))


def narrow(r, f: Fmt | None = None) -> str:
    """Бумага уже пиров того же рейтинга."""
    f = f or Fmt()
    txt = (f"{_head(r, f)} торгуется со спредом {f.bp(r['G-spread'])} к UST — на {f.bp(abs(r['Премия, бп']))} "
           f"уже медианы бумаг с похожим рейтингом и дюрацией ({f.bp(r['Медиана пиров'])}). Рынок оценивает риск "
           "эмитента ниже, чем у бумаг того же рейтинга, поэтому компенсация за кредитный риск здесь небольшая. "
           "Предлагаем не наращивать позицию; если для доборов по стратегии нужен источник финансирования, бумагу "
           "можно рассматривать среди кандидатов на сокращение.")
    return clean(txt + _reliability(r, f))


def widening(r, f: Fmt | None = None) -> str:
    """Аномальное расширение спреда между двумя портфелями."""
    f = f or Fmt()
    txt = (f"Спред {name(r['Позиция'])} ({r['Рейтинг']}) вырос с {f.bp(r['G-spread ранее'])} до "
           f"{f.bp(r['G-spread сейчас'])}, то есть на {f.bp(r['Изменение, бп'])}, тогда как медианное изменение "
           f"по бумагам того же рейтинга составило {f.bp(r['Медианное изменение группы, бп'], sign=True)}. "
           f"Расширение сверх группы — {f.bp(r['Сверх группы, бп'])}: движение связано с самим эмитентом, а не "
           "с рынком в целом.")
    if r.get("Рейтинг ранее") and r["Рейтинг ранее"] != r["Рейтинг"]:
        txt += f" За тот же период рейтинг изменился с {r['Рейтинг ранее']} на {r['Рейтинг']}."
    return clean(txt + (" Предлагаем проверить новости эмитента и не наращивать позицию, пока причина "
                        "расширения не ясна."))


# ---------- правила стратегии ----------

def _hy_funding(q: str | None, f: Fmt) -> str | None:
    """«…финансируется частичным сокращением Medium IG 49%→45,5%» -> «за счёт частичного сокращения Medium IG с 49% до 45,5% NAV»."""
    if not q:
        return None
    m = re.search(r"(частичн\w*\s+)?сокращени\w*\s+(.+?)\s+(\d+(?:[.,]\d+)?)\s*%\s*(?:→|->)\s*(\d+(?:[.,]\d+)?)\s*%", q)
    if not m:
        return None
    part = "частичного " if m.group(1) else ""
    return f.quote(f"за счёт {part}сокращения {m.group(2)} с {m.group(3)}% до {m.group(4)}% NAV")


def _long_funding(d: dict, f: Fmt) -> str:
    q = d.get("funding_quote")
    if q and re.search(r"t-?bill|т-?бил", q, re.I):
        return "за счёт T-bills"
    if q:
        return f"за счёт источника, указанного в стратегии («{f.quote(q)}»)"
    alt = d.get("funding_alt") or ""
    return f.quote(alt) if alt.startswith("за счёт") else "(источник финансирования в файле не указан)"


def _sleeve(d: dict) -> str:
    s = d.get("sleeve")
    return f"длинных IG-облигаций ({s})" if s else "длинных IG-облигаций"


def _calendar(cal, f: Fmt) -> str:
    months = [MONTH_IN[m] for m, _ in cal]
    return " или ".join(months) if months else ""


def _steps_text(steps: list, f: Fmt) -> str:
    parts = []
    for i, s in enumerate(steps):
        rng = f"{f.num(s['lower'] * 100)}–{f.num(s['upper'] * 100)}%"
        d, lvl = s["touch"] if s["touch"] else (None, None)
        step = f"{ORDINAL.get(s['n'], str(s['n']) + '-й')} шаг, {rng}"
        if d is None:
            parts.append(f"сейчас — {step}")
        elif i == 0:
            parts.append(f"{f.date(d)} она достигала {f.pct(lvl)} ({step})")
        else:
            parts.append(f"{f.date(d)} — {f.pct(lvl)} ({step})")
    if len(parts) <= 1:
        return "".join(parts)
    return ", ".join(parts[:-1]) + ", а " + parts[-1]


def action(a: dict, f: Fmt | None = None) -> str:
    """Действие по правилам стратегии — полным текстом с основанием и финансированием."""
    return clean(_action(a, f or Fmt()))


def _action(a: dict, f: Fmt) -> str:
    d = a.get("data") or {}
    k = d.get("kind")
    ten = f.label("10-летних UST")
    if k in ("ladder_now", "ladder_check"):
        gap = d["to"] - d["from"]
        head = (f"{_sleeve(d)} с {f.pctg(d['from'])} до целевых {f.pctg(d['to'])} NAV, то есть на "
                f"{f.num(gap * 100, 0 if abs(gap * 100 - round(gap * 100)) < 1e-9 else 1)} п.п., "
                f"{_long_funding(d, f)}.")
        steps = d["steps"]
        now = (f" ({f.date(d['ust_date'])}, {f.pct(d['ust_last'])})" if d.get("ust_last") is not None else "")
        if k == "ladder_now":
            return (f"Рекомендуем довести долю {head} Доходность {ten} находится в зоне лестницы, при которой "
                    f"стратегия предусматривает удлинение портфеля: {_steps_text(steps, f)}{now}. Добор длинных "
                    "бумаг на таких уровнях позволяет зафиксировать высокую доходность на длительный срок.")
        n = len([s for s in steps if s["touch"]])
        cal = _calendar(d.get("calendar") or [], f)
        return (f"Рекомендуем рассмотреть добор {head} После даты портфеля доходность {ten} "
                f"{TIMES.get(n, f'{n} раза')} заходила в зоны лестницы: {_steps_text(steps, f)}. Сейчас доходность "
                f"снова ниже этих уровней{now}. Если добор не был сделан, его стоит провести при следующем "
                f"возврате 10Y UST в зону лестницы" + (f" или на плановом пересмотре в {cal}" if cal else "")
                + ", как предусмотрено стратегией.")
    if k in ("hy_now", "hy_check"):
        full = re.sub(rf"^{re.escape(d['inst'])}\s*[-–—:]\s*", "", d.get("inst_name") or "")
        inst = d["inst"] + (f" ({f.quote(full)})" if full and full != d["inst"] else "")
        fund = _hy_funding(d.get("funding_quote"), f)
        fund_txt = (f" Финансирование — {fund}, как предусмотрено стратегией." if fund else
                    f" Источник финансирования по стратегии: «{f.quote(d['funding_quote'])}»." if d.get("funding_quote")
                    else "")
        touch = d.get("touch")
        if k == "hy_now":
            return (f"Рекомендуем увеличить долю {inst} с {f.pctg(d['from'])} до {f.pctg(d['to'])} NAV, то есть на "
                    f"{f.pp(d['to'] - d['from'], sign=False)} Спред индекса высокодоходных облигаций HY OAS составляет "
                    f"{f.bp(d['last'])} на {f.date(d['last_date'])} и выше триггера стратегии {f.bp(d['trigger'])}"
                    + (f"; впервые после даты портфеля триггер был превышен {f.date(touch[0])}" if touch else "")
                    + ". Широкие спреды HY повышают ожидаемую доходность сегмента, и стратегия предусматривает в этом "
                    "случае увеличение позиции." + fund_txt)
        return (f"Рекомендуем проверить, доведена ли доля {inst} до {f.pctg(d['to'])} NAV (в файле — "
                f"{f.pctg(d['from'])}). После даты портфеля спред HY OAS поднимался выше триггера стратегии "
                f"{f.bp(d['trigger'])}" + (f" (впервые {f.date(touch[0])}, {f.bp(touch[1])})" if touch else "")
                + (f", а сейчас он ниже — {f.bp(d['last'])} на {f.date(d['last_date'])}" if d.get("last") is not None else "")
                + ". Если добор не был сделан, его стоит провести при следующем превышении триггера." + fund_txt)
    if k == "calendar":
        return (f"Рекомендуем докупить {f.pctg(d['add'])} NAV в {_sleeve(d)} по календарному правилу стратегии, "
                f"{_long_funding(d, f)}. Плановый пересмотр {f.date(d['review'])} прошёл, а доходность {ten} так и "
                "не дошла до зон лестницы, поэтому стратегия предусматривает добор по графику, а не по уровню "
                "доходности.")
    if k == "next_review":
        return (f"Действий по правилам добора сейчас не требуется: доходность {ten} и спред HY OAS не достигали "
                f"уровней, заданных стратегией. Следующий плановый пересмотр — {f.date(d['review'])}.")
    return f"{f.quote(a['Действие'])}. Основание: {f.quote(a['Основание'])}. Финансирование — {f.quote(a['Финансирование'])}."


# ---------- портфель против целей ----------

def targets(t: pd.DataFrame, m: dict, proforma: dict | None, f: Fmt | None = None) -> str | None:
    """YTM и дюрация против целей, с оценкой того, сколько отставания закрывает целевая аллокация."""
    f = f or Fmt()
    t = t.set_index("key")
    tg = t.loc["ytm_gross"]
    if pd.isna(tg["Цель"]):
        return None
    gap = tg["Отклонение"]
    side = "ниже" if gap < 0 else "выше"
    txt = (f"Доходность портфеля к погашению составляет {f.pct(m['ytm_gross'])} ({f.pct(m['ytm_net'])} после "
           f"комиссии) и на {f.bp(abs(gap) * 10000)} {side} целевых {f.pct(tg['Цель'])}.")
    td = t.loc["mod_duration", "Цель"] if "mod_duration" in t.index else None
    if td is not None and pd.notna(td):
        dd = m["mod_duration"] - td
        txt += (f" Модифицированная дюрация — {f.num(m['mod_duration'])} при цели {f.num(td)}: портфель "
                + ("короче целевого, то есть процентный риск ниже заложенного в стратегии, но и дополнительная "
                   "доходность от длинных бумаг не получена." if dd < 0 else
                   "длиннее целевого, то есть чувствительность к росту ставок выше заложенной в стратегии."))
    if proforma:
        txt += (f" При доведении сегментов до целевой аллокации и нынешнем составе бумаг YTM составила бы "
                f"{f.pct(proforma['ytm_gross'])}, дюрация — {f.num(proforma['mod_duration'])}, G-spread — "
                f"{f.bp(proforma['gspread'])}.")
        if gap < 0:
            closed = (proforma["ytm_gross"] - m["ytm_gross"]) / abs(gap) if gap else 0
            txt += (" Значит, бо́льшую часть отставания от цели закрывает выполнение целевой аллокации."
                    if closed >= 0.5 else
                    " Выполнение целевой аллокации закрывает лишь часть отставания от цели; остальное потребует "
                    "замены бумаг на более доходные.")
    return clean(txt)


def allocation(al: pd.DataFrame, f: Fmt | None = None) -> str | None:
    """Отклонения сегментов от целевой аллокации."""
    f = f or Fmt()
    off = al[al["Статус"] != "в пределах"]
    if off.empty:
        return None
    under = [f"{r['Sleeve']} — {f.pct(r['Текущее'], 1)} при цели {f.pct(r['Цель'], 1)}" for _, r in off.iterrows()
             if r["Отклонение"] < 0]
    over = [f"{r['Sleeve']} — {f.pct(r['Текущее'], 1)} при цели {f.pct(r['Цель'], 1)}" for _, r in off.iterrows()
            if r["Отклонение"] > 0]
    parts = []
    if under:
        parts.append("Недовес по сравнению с целевой аллокацией: " + "; ".join(under) + ".")
    if over:
        parts.append("Перевес: " + "; ".join(over) + ".")
    parts.append("Перераспределение между этими сегментами и есть путь к целевым YTM и дюрации; порядок и сроки "
                 "задают правила стратегии (лестница по 10Y UST, триггер HY OAS и календарные пересмотры).")
    return clean(" ".join(parts))


SUBJECT = {"Эмитент": "эмитент", "Страна": "страна", "Сектор (укрупнённо)": "сектор", "Регион": "регион"}


def subject(r, gen: bool = False) -> str:
    """Объект лимита для текста: «сектор Energy» / в родительном падеже «сектора Energy»."""
    obj = str(r["Объект"])
    if obj.startswith("EM всего"):
        return "бумаг развивающихся рынков (EM)" if gen else "бумаги развивающихся рынков (EM)"
    if obj.startswith("HY всего"):
        return "бумаг категории HY" if gen else "бумаги категории HY"
    kind = GEN.get(r["Тип"], r["Тип"].lower()) if gen else SUBJECT.get(r["Тип"], r["Тип"].lower())
    return f"{kind} {obj}"


def concentration(r, f: Fmt | None = None) -> str:
    """Лимит концентрации превышен или близок."""
    f = f or Fmt()
    who = GEN.get(r["Тип"], r["Тип"].lower())
    obj = str(r["Объект"])
    if obj.startswith("EM всего"):
        head = "Доля бумаг развивающихся рынков (EM)"
    elif obj.startswith("HY всего"):
        head = "Доля бумаг категории HY (рейтинг ниже BBB-)"
    else:
        head = f"Доля {who} {obj}"
    head += f" составляет {f.pct(r['Доля'], 1)} NAV"
    basis = _basis(str(r["Основание лимита"]), f)
    if r["Статус"] == "превышен":
        return clean(f"{head} и превышает лимит {f.pct(r['Лимит'], 1)} на {f.pp(r['Доля'] - r['Лимит'], sign=False)}; "
                     f"{basis}. Превышение усиливает зависимость портфеля от одного {who}. Предлагаем сократить долю "
                     "до лимита или согласовать исключение с командой.")
    return clean(f"{head} при лимите {f.pct(r['Лимит'], 1)}, запас — {f.pp(r['Лимит'] - r['Доля'], sign=False)}; "
                 f"{basis}. Новые покупки в этом направлении стоит ограничить, чтобы не выйти за лимит.")


def _basis(b: str, f: Fmt) -> str:
    """Основание лимита для текста."""
    m = re.match(r"допущение \((.+)\)$", b)
    if b == "допущение":
        return "лимит — допущение инструмента, его можно изменить в панели слева"
    if m and m.group(1).startswith("UCITS"):
        return f"лимит — допущение по правилу {f.label(m.group(1))}"
    if m:
        return f"лимит — допущение ({f.quote(m.group(1))})"
    if b.startswith("дашборд:"):
        return f"лимит указан на дашборде («{f.quote(b.split(':', 1)[1].strip())}»)"
    return f"основание лимита — {f.quote(b)}"


# ---------- сравнение и рынок ----------

COMPONENT_TEXT = {"Рынок UST (сдвиг кривой на сроки бумаг)": "сдвиг кривой UST",
                  "Спред и прочее по оставшимся бумагам": "изменение спредов по оставшимся бумагам",
                  "Изменение значений по оставшимся бумагам": "изменение доходностей оставшихся бумаг",
                  "Перевзвешивание оставшихся бумаг": "перевзвешивание оставшихся бумаг",
                  "Новые бумаги": "покупка новых бумаг", "Выбывшие бумаги": "выбытие бумаг"}


def comparison(cmp, f: Fmt | None = None) -> str:
    """Что изменилось с прошлого портфеля и что больше всего повлияло на доходность."""
    f = f or Fmt()
    mt = cmp.metrics.set_index("key")
    at = cmp.attribution["YTM, бп"]
    comp = at[at["Компонент"] != "Итого изменение портфеля"]
    main = comp.loc[comp["Вклад"].abs().idxmax()]
    return clean(
        f"С {f.date(cmp.prev_date)} доходность портфеля изменилась на {f.bp_from(mt.loc['ytm_gross', 'Изменение'])}, "
        f"G-spread — на {f.bp(mt.loc['gspread', 'Изменение'], sign=True, d=1)}, модифицированная дюрация — на "
        f"{f.num(mt.loc['mod_duration', 'Изменение'], sign=True)}. Больше всего на доходность повлиял фактор "
        f"«{COMPONENT_TEXT.get(main['Компонент'], main['Компонент'])}»: {f.bp(main['Вклад'], sign=True, d=1)}.")


def market(mk, f: Fmt | None = None) -> str | None:
    """Изменение 10Y UST и HY OAS с даты портфеля и их роль в правилах стратегии."""
    f = f or Fmt()
    ca, cl = mk.curve_asof, mk.curve_latest
    if not (ca and cl):
        return None
    d10 = (cl.at(10) - ca.at(10)) * 10000
    txt = (f"С даты портфеля доходность {f.label('10-летних UST')} изменилась с {f.pct(ca.at(10))} до "
           f"{f.pct(cl.at(10))} ({f.bp(d10, sign=True)})")
    if mk.hy_asof and mk.hy_asof[0] and mk.hy_latest and mk.hy_latest[0]:
        txt += (f", спред индекса высокодоходных облигаций HY OAS — с {f.bp(mk.hy_asof[1])} до "
                f"{f.bp(mk.hy_latest[1])}")
    txt += (". Эти два показателя определяют правила добора стратегии: уровень 10Y UST — удлинение портфеля, "
            "HY OAS — увеличение доли HY.")
    return clean(txt)


# ---------- оценка на текущую дату и стресс-тест ----------

def estimate(est, f: Fmt | None = None) -> str:
    """Оценка доходности и результата портфеля на последнюю рыночную дату по индексам."""
    f = f or Fmt()
    pe = est.portfolio
    return clean(
        f"По индексам ICE BofA (FRED) и кривой Treasury доходность портфеля на {f.date(est.est_date)} оценивается "
        f"в {f.pct(pe['ytm_est'])} ({f.bp_from(pe['ytm_est'] - pe['ytm_file'])} к файлу), G-spread — в "
        f"{f.bp(pe['gs_est'])} ({f.bp(pe['gs_est'] - pe['gs_file'], sign=True)}). За {f.int(pe['days'])} дн. с "
        f"даты портфеля результат составил около {f.pct(pe['total'], sign=True)} NAV: изменение цен дало "
        f"{f.pct(pe['price'], sign=True)}, купонный доход — {f.pct(pe['carry'], sign=True)}. Это оценка по "
        "индексам, а не котировки бумаг: спред каждой бумаги сдвинут так же, как OAS её индекса.")


def stress(stp: dict, f: Fmt | None = None) -> str:
    """Самый тяжёлый стандартный сценарий и что он значит для стратегии."""
    f = f or Fmt()
    sm = stp["summary"]
    w = sm.loc[sm["P&L портфеля, % NAV"].idxmin()]
    if w["P&L портфеля, % NAV"] >= 0:
        return "Ни один из стандартных сценариев не приводит к убытку портфеля."
    return clean(
        f"Самый тяжёлый из стандартных сценариев — «{f.quote(w['Сценарий'])}»: портфель теряет около "
        f"{f.pct(abs(w['P&L портфеля, % NAV']))} NAV, больше всего — позиция {name(f.quote(w['Худшая позиция']))} "
        f"({f.pct(w['P&L худшей позиции, % NAV'], sign=True)} NAV). Потери пропорциональны дюрации, поэтому "
        "удлинение портфеля по правилам стратегии увеличит их в сценариях роста ставок.")
