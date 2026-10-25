"""Проверки качества данных портфеля.

Каждая проверка возвращает записи вида:
уровень (Ошибка / Предупреждение / Инфо), категория, позиция, поле,
значение в файле, расчётное или ожидаемое значение, комментарий.
Проверки только сравнивают данные файла между собой и с расчётом — внешние данные не используются.
"""
from __future__ import annotations

import re

import pandas as pd

from . import ratings
from .bondmath import isin_valid, modified_duration, years_between
from .loader import Snapshot, norm

SEVERITY_ORDER = {"Ошибка": 0, "Предупреждение": 1, "Инфо": 2}
COLUMNS = ["Уровень", "Категория", "Позиция", "Поле", "В файле", "Расчёт / ожидание", "Комментарий"]


def pct(x, d=2):
    return "—" if x is None or pd.isna(x) else f"{x * 100:.{d}f}%".replace(".", ",")


def num(x, d=2):
    return "—" if x is None or pd.isna(x) else f"{x:.{d}f}".replace(".", ",")


def dfmt(d):
    return "—" if d is None or pd.isna(d) else d.strftime("%d.%m.%Y")


class _Issues:
    def __init__(self):
        self.rows = []

    def add(self, severity, category, message, position="Портфель", field="", file_value="", expected=""):
        self.rows.append({"Уровень": severity, "Категория": category, "Позиция": position, "Поле": field,
                          "В файле": file_value, "Расчёт / ожидание": expected, "Комментарий": message})


def run_checks(snap: Snapshot, cfg: dict) -> pd.DataFrame:
    q = cfg["quality"]
    tol = q["reported_tolerance"]
    fee = cfg["analysis"]["fee"]
    iss = _Issues()
    p = snap.positions

    for n in snap.load_notes:
        iss.add(n["severity"], n["category"], n["message"])

    _check_date(snap, iss)
    _check_weights(p, q, iss)
    present = set(snap.detected.get("columns", {})) if snap.detected else None
    if present is not None and "market_value" in present:
        present.add("weight")
    _check_fields(p, iss, present)
    _check_name_vs_columns(p, snap, q, iss)
    _check_ratings(p, iss)
    _check_ranges(p, snap, q, iss)
    _check_isin(p, iss)
    _check_countries(p, q, iss)
    _check_totals(p, snap, tol, fee, iss)
    _check_allocation(p, snap, tol, iss)
    _check_breakdown(p, snap, tol, iss)
    _check_dashboard_claims(p, snap, tol, iss)
    _check_dashboard_text(snap, iss)

    df = pd.DataFrame(iss.rows, columns=COLUMNS)
    if not df.empty:
        df["_o"] = df["Уровень"].map(SEVERITY_ORDER)
        df = df.sort_values(["_o", "Категория"], kind="stable").drop(columns="_o").reset_index(drop=True)
    return df


# ---------- отдельные проверки ----------

def _check_date(snap, iss):
    if not snap.as_of:
        iss.add("Ошибка", "Дата портфеля", "Дата портфеля не найдена ни в заголовках листов, ни в имени файла. "
                "От неё зависят срок до погашения каждой бумаги, выбор кривой UST для пересчёта G-spread и "
                "сравнение с рынком, поэтому без даты эти расчёты неточны. Дату нужно указать в блоке "
                "«Распознавание файла» над вкладками.")
        return
    dates = {d for _, d in snap.as_of_candidates}
    srcs = "; ".join(f"{s}: {dfmt(d)}" for s, d in snap.as_of_candidates)
    if len(dates) > 1:
        iss.add("Предупреждение", "Дата портфеля", f"В файле указаны разные даты ({srcs}). Для расчётов "
                f"взята {dfmt(snap.as_of)}. Если портфель собран на другую дату, её нужно указать в блоке "
                "«Распознавание файла»: от даты зависят сроки до погашения и кривая UST.",
                field="Дата", file_value=srcs)
    else:
        iss.add("Инфо", "Дата портфеля", f"Дата портфеля — {dfmt(snap.as_of)} (источник: {srcs}). По ней "
                "считаются сроки до погашения и выбирается кривая UST.")


def _check_weights(p, q, iss):
    s = p["weight"].sum(skipna=True)
    tol_pp = q["weight_sum_tolerance_pp"]
    if abs(s - 1) * 100 > tol_pp:
        iss.add("Ошибка", "Веса", f"Сумма весов позиций равна {pct(s, 3)} и отклоняется от 100% больше чем на "
                f"{num(tol_pp)} п.п. Из-за этого искажаются средневзвешенные YTM, дюрация и спред портфеля. "
                "Нужно проверить, все ли позиции попали в файл и нет ли лишних строк, например итоговых.",
                field="Weight", file_value=pct(s, 3), expected="100%")
    else:
        iss.add("Инфо", "Веса", f"Сумма весов позиций — {pct(s, 3)}, в пределах допуска ±{num(tol_pp)} п.п. "
                "от 100%.")
    for _, r in p[p["weight"] < 0].iterrows():
        iss.add("Ошибка", "Веса", f"Вес позиции отрицательный ({pct(r['weight'])}). В портфеле облигаций без "
                "коротких позиций это признак ошибки ввода; такой вес занижает средневзвешенные метрики. Вес нужно "
                "сверить с исходной выгрузкой.", r["label"], "Weight", pct(r["weight"]))


REQUIRED_BY_TYPE = {
    "bond": ["sleeve", "weight", "isin", "coupon", "maturity", "ytm", "gspread", "mod_duration",
             "region", "country", "sector", "rating", "credit_bucket"],
    "fund": ["sleeve", "weight", "isin", "ytm", "gspread", "mod_duration", "region", "rating"],
    "cash": ["sleeve", "weight", "ytm", "mod_duration"],
}
OPTIONAL_INFO = {"fund": ["coupon", "maturity"], "cash": ["isin", "coupon", "maturity", "gspread"]}
TYPE_LABEL = {"bond": "облигация", "fund": "фонд/ETF", "cash": "денежная позиция"}
TYPE_GEN = {"bond": "облигации", "fund": "фонда (ETF)", "cash": "денежной позиции"}


def _check_fields(p, iss, present=None):
    """Пустые ячейки в колонках, которые в файле есть. Отсутствующие колонки целиком
    уже отмечены при загрузке одним предупреждением."""
    from .loader import COLUMN_LABELS
    for _, r in p.iterrows():
        t = r["instrument_type"]
        empty = [c for c in REQUIRED_BY_TYPE[t] if c in r and (present is None or c in present)
                 and (r[c] is None or pd.isna(r[c]))]
        if empty:
            names = ", ".join(COLUMN_LABELS[c] for c in empty)
            iss.add("Ошибка", "Пустые поля", f"У позиции ({TYPE_LABEL[t]}) не заполнены поля, нужные для расчётов: "
                    f"{names}. Без них позиция выпадает из соответствующих метрик: без YTM — из доходности "
                    "портфеля, без рейтинга — из кредитного профиля, без дюрации — из процентного риска. Значения "
                    "нужно запросить у команды или внести через «Поправки аналитика».", r["label"], names)
        expected_empty = [c for c in OPTIONAL_INFO.get(t, []) if (present is None or c in present)
                          and (r[c] is None or pd.isna(r[c]))]
        if expected_empty:
            names = ", ".join(COLUMN_LABELS[c] for c in expected_empty)
            iss.add("Инфо", "Пустые поля",
                    f"Не заполнены поля {names}. Для {TYPE_GEN[t]} это допустимо и на анализ не влияет: G-spread "
                    "по такой позиции не пересчитывается, в метриках используются значения из файла.",
                    r["label"], names)


def _check_name_vs_columns(p, snap, q, iss):
    for _, r in p.iterrows():
        nm, cm = r["name_maturity"], r["maturity"]
        if nm is not None and not pd.isna(nm) and cm is not None and not pd.isna(cm) and nm != cm:
            msg = (f"Дата погашения в названии бумаги ({dfmt(nm)}) не совпадает с колонкой Maturity ({dfmt(cm)}). "
                   "От даты погашения зависят срок до погашения, расчётная дюрация и пересчитанный G-spread, "
                   "поэтому одну из дат нужно исправить.")
            md_note = _which_date_fits(r, snap, nm, cm)
            iss.add("Ошибка", "Погашение", msg + (" " + md_note if md_note else "")
                    + " Предлагаем сверить дату с проспектом выпуска и при необходимости внести поправку.",
                    r["label"], "Maturity", dfmt(cm), f"в названии: {dfmt(nm)}")
        nc, cc = r["name_coupon"], r["coupon"]
        if nc is not None and cc is not None and not pd.isna(nc) and not pd.isna(cc) \
                and abs(nc - cc) > q.get("coupon_tolerance", 0.0001):
            iss.add("Ошибка", "Купон", f"Купон в названии бумаги ({pct(nc, 3)}) не совпадает с колонкой Coupon "
                    f"({pct(cc, 3)}). Купон используется в расчёте дюрации, поэтому значение нужно сверить с "
                    "проспектом выпуска и исправить в файле или через «Поправки аналитика».",
                    r["label"], "Coupon", pct(cc, 3), f"в названии: {pct(nc, 3)}")


def _which_date_fits(r, snap, name_date, col_date):
    """Какая дата погашения согласуется с mod. duration из файла (по расчёту)."""
    if not snap.as_of or pd.isna(r["mod_duration"]):
        return ""
    md_col = modified_duration(snap.as_of, col_date, r["coupon"], r["ytm"])
    md_name = modified_duration(snap.as_of, name_date, r["coupon"], r["ytm"])
    if md_col is None or md_name is None:
        return ""
    fits = "названия" if abs(md_name - r["mod_duration"]) < abs(md_col - r["mod_duration"]) else "колонки"
    return (f"Расчётная модифицированная дюрация по дате из колонки — {num(md_col)}, по дате из названия — "
            f"{num(md_name)}; в файле указана {num(r['mod_duration'])}, это ближе к дате из {fits}.")


def _check_ratings(p, iss):
    for _, r in p.iterrows():
        rt = r["rating"]
        if rt is None or pd.isna(rt):
            continue
        if ratings.normalize(rt) is None:
            iss.add("Предупреждение", "Рейтинг", f"Рейтинг «{rt}» не распознан: ожидается шкала S&P/Fitch "
                    "(например, BBB+) или Moody's (Baa1). Без рейтинга бумага не попадает в средний рейтинг, доли "
                    "IG и HY и сравнение с пирами. Значение нужно исправить в файле или через «Поправки аналитика».",
                    r["label"], "Rating", rt)
            continue
        letter, ig = ratings.letter(rt), ratings.is_ig(rt)
        b = ratings.parse_bucket(r["credit_bucket"])
        bucket = r["credit_bucket"] or ""
        if b["grades"] and letter not in b["grades"]:
            iss.add("Ошибка", "Рейтинг / категория",
                    f"Рейтинг {rt} не соответствует кредитной категории «{bucket}», в которой указаны ступени "
                    f"{', '.join(sorted(b['grades']))}. Категория используется в распределении портфеля, а рейтинг — "
                    "в среднем рейтинге и сигналах по спредам, поэтому расхождение искажает кредитный профиль. "
                    "Нужно уточнить, что верно — рейтинг или категория.",
                    r["label"], "Credit bucket", bucket, f"ступень {letter}")
        elif b["ig"] and not ig:
            iss.add("Ошибка", "Рейтинг / категория", f"Кредитная категория «{bucket}» относится к инвестиционному "
                    f"уровню, а рейтинг {rt} — к HY. В расчётах бумага учитывается по рейтингу, то есть как HY, и "
                    "увеличивает долю HY в портфеле. Категорию или рейтинг нужно привести в соответствие.",
                    r["label"], "Credit bucket", bucket, rt)
        elif b["hy"] and ig and not b["fund"]:
            iss.add("Ошибка", "Рейтинг / категория", f"Кредитная категория «{bucket}» относится к HY, а рейтинг "
                    f"{rt} — к инвестиционному уровню. В расчётах бумага учитывается по рейтингу, то есть как IG. "
                    "Категорию или рейтинг нужно привести в соответствие.",
                    r["label"], "Credit bucket", bucket, rt)
        sleeve_tokens = set(re.split(r"[^A-Za-z]+", r["sleeve"] or ""))
        if "IG" in sleeve_tokens and not ig:
            iss.add("Ошибка", "Рейтинг / sleeve", f"Бумага с рейтингом {rt} (HY) находится в IG-сегменте "
                    f"«{r['sleeve']}». Это противоречит назначению сегмента. Предлагаем проверить, не было ли "
                    "понижения рейтинга, и решить, оставлять ли позицию в сегменте.",
                    r["label"], "Sleeve", r["sleeve"], rt)
        if "HY" in sleeve_tokens and ig:
            iss.add("Предупреждение", "Рейтинг / sleeve", f"Бумага с рейтингом {rt} (инвестиционный уровень) "
                    f"находится в HY-сегменте «{r['sleeve']}». Для HY-сегмента это нетипично; стоит проверить, верно "
                    "ли указаны рейтинг и сегмент.",
                    r["label"], "Sleeve", r["sleeve"], rt)


def _check_ranges(p, snap, q, iss):
    for _, r in p.iterrows():
        y, g, md, c = r["ytm"], r["gspread"], r["mod_duration"], r["coupon"]
        if pd.notna(y) and not (q["ytm_min"] <= y <= q["ytm_max"]):
            iss.add("Предупреждение", "Аномальные значения",
                    f"YTM {pct(y)} вне ожидаемого диапазона {pct(q['ytm_min'])}–{pct(q['ytm_max'])}. Это может "
                    "быть ошибка ввода, например значение в процентах вместо долей, и оно заметно влияет на "
                    "доходность портфеля. Значение нужно сверить с источником котировки.", r["label"], "YTM", pct(y))
        if pd.notna(g) and not (q["gspread_min_bp"] <= g <= q["gspread_max_bp"]):
            iss.add("Предупреждение", "Аномальные значения",
                    f"G-spread {num(g, 0)} б.п. вне ожидаемого диапазона {q['gspread_min_bp']}–"
                    f"{q['gspread_max_bp']} б.п. Значение может быть ошибкой ввода и искажает спред портфеля и "
                    "сигналы по пирам. Его нужно сверить с источником котировки.",
                    r["label"], "G-spread", num(g, 0))
        if pd.notna(c) and not (0 <= c <= q["coupon_max"]):
            iss.add("Предупреждение", "Аномальные значения", f"Купон {pct(c)} вне разумного диапазона "
                    f"0–{pct(q['coupon_max'])}. Значение нужно сверить с проспектом выпуска.",
                    r["label"], "Coupon", pct(c))
        if pd.notna(md) and md <= 0:
            iss.add("Ошибка", "Аномальные значения", f"Модифицированная дюрация не положительная ({num(md)}). "
                    "Для облигации это невозможно; значение искажает дюрацию портфеля и стресс-тест и должно быть "
                    "исправлено.", r["label"], "Mod. duration", num(md))
        mat = r["maturity"]
        if snap.as_of and mat is not None and not pd.isna(mat):
            if mat <= snap.as_of:
                iss.add("Ошибка", "Погашение", f"Дата погашения {dfmt(mat)} не позже даты портфеля "
                        f"{dfmt(snap.as_of)}: бумага уже погашена или дата указана неверно. Позицию нужно исключить "
                        "из портфеля или исправить дату погашения.",
                        r["label"], "Maturity", dfmt(mat), f"> {dfmt(snap.as_of)}")
                continue
            yrs = years_between(snap.as_of, mat)
            if pd.notna(md) and md > yrs + 0.01:
                excess = md - yrs
                big = excess > q["duration_over_maturity_error"]
                iss.add("Ошибка" if big else "Предупреждение", "Дюрация",
                        f"Модифицированная дюрация ({num(md)}) больше срока до погашения ({num(yrs)} г.) на "
                        f"{num(excess)} г. "
                        + ("Для облигации так не бывает, значит, ошибка в дюрации или в дате погашения. Её нужно "
                           "исправить: от дюрации зависят процентный риск портфеля и стресс-тест." if big
                           else "Расхождение небольшое: скорее всего, это округление или дюрация, посчитанная на "
                                "более раннюю дату. На метрики портфеля оно почти не влияет."),
                        r["label"], "Mod. duration", num(md), f"срок до погашения {num(yrs)} г.")
                continue
            # Сверка дюрации с расчётной (только если даты в названии и колонке не расходятся).
            nm = r["name_maturity"]
            if r["instrument_type"] == "bond" and pd.notna(md) and pd.notna(c) and pd.notna(y) \
                    and (nm is None or pd.isna(nm) or nm == mat):
                calc = modified_duration(snap.as_of, mat, c, y)
                if calc is not None:
                    diff = md - calc
                    if abs(diff) > q["duration_check_abs"] and abs(diff) > q["duration_check_rel"] * calc:
                        iss.add("Предупреждение", "Дюрация",
                                f"Модифицированная дюрация в файле ({num(md)}) заметно расходится с расчётной "
                                f"по купону, YTM и дате погашения ({num(calc)}). Частая причина — дюрация "
                                "посчитана к дате колла (досрочного погашения). Предлагаем проверить, есть ли у "
                                "выпуска колл и к какой дате считается дюрация в файле.",
                                r["label"], "Mod. duration", num(md), f"расчёт: {num(calc)}")


def _check_isin(p, iss):
    for _, r in p.iterrows():
        isin = r["isin"]
        if isin is not None and pd.notna(isin) and not isin_valid(isin):
            iss.add("Предупреждение", "ISIN", f"У ISIN «{isin}» неверный формат или контрольная цифра. По ISIN "
                    "позиции сопоставляются между портфелями, поэтому из-за ошибки бумага может считаться новой. "
                    "ISIN нужно сверить с источником.", r["label"], "ISIN", isin)
    dup = p[p["isin"].notna() & p["isin"].duplicated(keep=False)]
    for isin, g in dup.groupby("isin"):
        iss.add("Ошибка", "ISIN", "Один и тот же ISIN указан в нескольких строках: " + ", ".join(g["label"])
                + ". Если это одна бумага, строки нужно объединить, иначе ISIN одной из них неверен. От этого "
                "зависят концентрация по эмитенту и сравнение портфелей.",
                field="ISIN", file_value=isin)


def _check_countries(p, q, iss):
    bad = {norm(x) for x in q["non_country_values"]}
    for _, r in p.iterrows():
        if r["country"] and norm(r["country"]) in bad:
            iss.add("Предупреждение", "Страна",
                    f"В колонке Country указан регион «{r['country']}», а не страна. Поэтому позиция не попадает "
                    "в концентрацию по странам и лимит по стране для неё не проверяется. Предлагаем указать "
                    "страну эмитента.", r["label"], "Country", r["country"])


def _wsum(p, col):
    m = p["weight"].notna() & p[col].notna()
    return float((p.loc[m, "weight"] * p.loc[m, col]).sum())


def _check_totals(p, snap, tol, fee, iss):
    calc = {"weight": p["weight"].sum(), "ytm": _wsum(p, "ytm"),
            "gspread": _wsum(p, "gspread"), "mod_duration": _wsum(p, "mod_duration")}
    spec = {"weight": ("Сумма весов", tol["weight_pp"] / 100, lambda x: pct(x, 3)),
            "ytm": ("Средневзвешенная YTM", tol["ytm"], lambda x: pct(x, 3)),
            "gspread": ("Средневзвешенный G-spread, бп", tol["gspread_bp"], lambda x: num(x, 1)),
            "mod_duration": ("Средневзвешенная mod. duration", tol["duration"], lambda x: num(x, 3))}
    corrected = getattr(snap, "corrected_fields", set()) or set()
    after_fix = " Это ожидаемо: итог в файле посчитан до поправок аналитика."
    t = snap.file_totals
    if t:
        for k, (label, tl, f) in spec.items():
            v = t.get(k)
            if v is None:
                continue
            if abs(v - calc[k]) > tl:
                if k in corrected:
                    iss.add("Инфо", "Итоги листа портфеля", f"{label}: итог в файле ({f(v)}) расходится с "
                            f"расчётом по позициям ({f(calc[k])}).{after_fix}",
                            field=f"строка {t['excel_row']}", file_value=f(v), expected=f(calc[k]))
                    continue
                iss.add("Ошибка", "Итоги листа портфеля", f"{label}: итог в файле ({f(v)}) не сходится с "
                        f"расчётом по позициям ({f(calc[k])}). Значит, итоговая строка посчитана по другим данным "
                        "или в позициях есть ошибка. В анализе используются значения по позициям; итог в файле "
                        "стоит пересчитать.",
                        field=f"строка {t['excel_row']}", file_value=f(v), expected=f(calc[k]))
            else:
                iss.add("Инфо", "Итоги листа портфеля", f"{label}: итог в файле сходится с расчётом по позициям "
                        f"({f(calc[k])}).")
    m = snap.dashboard_metrics
    dash = {"ytm_gross": calc["ytm"], "ytm_net": calc["ytm"] - fee, "mod_duration": calc["mod_duration"],
            "gspread": calc["gspread"]}
    dtol = {"ytm_gross": tol["ytm"], "ytm_net": tol["ytm"], "mod_duration": tol["duration"], "gspread": tol["gspread_bp"]}
    dfmtf = {"ytm_gross": lambda x: pct(x, 3), "ytm_net": lambda x: pct(x, 3),
             "mod_duration": lambda x: num(x, 3), "gspread": lambda x: num(x, 1)}
    for k, calc_v in dash.items():
        if k not in m:
            continue
        v = m[k]["current"]
        if v is None:
            continue
        if abs(v - calc_v) > dtol[k]:
            src_field = "ytm" if k.startswith("ytm") else k
            if src_field in corrected:
                iss.add("Инфо", "Дашборд", f"Значение «{m[k]['label']}» на дашборде ({dfmtf[k](v)}) расходится с "
                        f"расчётом по позициям ({dfmtf[k](calc_v)}).{after_fix}",
                        field=f"00 Dashboard, строка {m[k]['row']}", file_value=dfmtf[k](v), expected=dfmtf[k](calc_v))
                continue
            iss.add("Ошибка", "Дашборд", f"Значение «{m[k]['label']}» на дашборде ({dfmtf[k](v)}) не сходится с "
                    f"расчётом по позициям ({dfmtf[k](calc_v)}). Вероятно, дашборд не обновлён после изменения "
                    "позиций; в анализе используются данные по позициям.",
                    field=f"00 Dashboard, строка {m[k]['row']}", file_value=dfmtf[k](v), expected=dfmtf[k](calc_v))
    if m.get("ytm_gross") and m.get("ytm_net") and m["ytm_gross"].get("target") and m["ytm_net"].get("target"):
        implied = m["ytm_gross"]["target"] - m["ytm_net"]["target"]
        if abs(implied - fee) > tol["ytm"]:
            iss.add("Предупреждение", "Дашборд", f"Разница между целевыми YTM брутто и нетто на дашборде "
                    f"({pct(implied)}) не равна комиссии ({pct(fee)}). Либо одна из целей указана неверно, либо "
                    "комиссия по стратегии другая; от этого зависит оценка отставания от цели. Комиссию можно "
                    "изменить в панели слева.",
                    field="00 Dashboard", file_value=pct(implied), expected=pct(fee))


def _check_allocation(p, snap, tol, iss):
    a = snap.allocation
    if a is None or a.empty:
        return
    tsum = a["target"].sum(skipna=True)
    if abs(tsum - 1) > tol["weight_pp"] / 100:
        iss.add("Ошибка", "Аллокация", f"Целевая аллокация на листе Allocation в сумме даёт {pct(tsum)}, а не "
                "100%. Из-за этого отклонения сегментов от цели считаются неточно; цели нужно уточнить у команды.",
                field="Target",
                file_value=pct(tsum), expected="100%")
    by_sleeve = p.groupby("sleeve")["weight"].sum()
    for _, r in a.iterrows():
        if r["match"] == "частичное":
            iss.add("Инфо", "Аллокация", f"Сегмент «{r['sleeve']}» на листе Allocation сопоставлен с сегментом "
                    f"портфеля «{r['portfolio_sleeve']}» по началу названия. Если названия сегментов менялись, "
                    "сопоставление стоит проверить.", r["sleeve"], "Sleeve")
        elif r["match"] in ("не найдено", "неоднозначное"):
            iss.add("Ошибка", "Аллокация", f"Сегмент «{r['sleeve']}» с листа Allocation не найден среди сегментов "
                    f"портфеля (сопоставление: {r['match']}). Для него не считается отклонение от цели; нужно "
                    "проверить написание названия.",
                    r["sleeve"], "Sleeve")
            continue
        calc = by_sleeve.get(r["portfolio_sleeve"], 0.0)
        if r["current"] is not None and pd.notna(r["current"]) and abs(r["current"] - calc) > tol["weight_pp"] / 100:
            if {"weight", "sleeve"} & (getattr(snap, "corrected_fields", set()) or set()):
                iss.add("Инфо", "Аллокация", f"Текущая доля сегмента на листе Allocation ({pct(r['current'])}) "
                        f"расходится с суммой весов его позиций ({pct(calc)}). Это ожидаемо: лист посчитан до поправок "
                        "аналитика.", r["sleeve"], "Current", pct(r["current"]), pct(calc))
            else:
                iss.add("Ошибка", "Аллокация", f"Текущая доля сегмента на листе Allocation ({pct(r['current'])}) "
                        f"не сходится с суммой весов его позиций ({pct(calc)}). В анализе используется сумма по "
                        "позициям; лист Allocation, вероятно, не обновлён.",
                        r["sleeve"], "Current", pct(r["current"]), pct(calc))
        if r["gap"] is not None and pd.notna(r["gap"]) and r["current"] is not None and pd.notna(r["current"]):
            if abs(r["gap"] - (r["target"] - r["current"])) > 1e-6:
                iss.add("Предупреждение", "Аллокация", f"Колонка Gap to target ({pct(r['gap'])}) не равна разнице "
                        f"Target − Current ({pct(r['target'] - r['current'])}). На анализ это не влияет, отклонение "
                        "считается заново, но в файле есть внутреннее противоречие.", r["sleeve"], "Gap",
                        pct(r["gap"]), pct(r["target"] - r["current"]))
    mapped = set(a["portfolio_sleeve"].dropna())
    for s in by_sleeve.index:
        if s not in mapped:
            iss.add("Ошибка", "Аллокация", f"Сегмента «{s}» с долей {pct(by_sleeve[s])} нет на листе Allocation, "
                    "поэтому цель для него не задана и отклонение не считается. Цель нужно добавить на лист.",
                    s, "Sleeve")


def _check_breakdown(p, snap, tol, iss):
    for key, col, label in (("region", "region", "регионам"), ("rating", "rating", "рейтингам")):
        vals = snap.breakdown.get(key)
        if not vals:
            continue
        calc = p.groupby(col)["weight"].sum()
        bad = []
        for name in set(vals) | set(calc.index):
            fv, cv = vals.get(name, 0.0), float(calc.get(name, 0.0))
            if abs(fv - cv) > tol["weight_pp"] / 100:
                bad.append(f"{name}: {pct(fv)} в файле, {pct(cv)} по позициям")
        if bad and {col, "weight"} & (getattr(snap, "corrected_fields", set()) or set()):
            iss.add("Инфо", "Breakdown", f"Разбивка по {label} на листе 03 Breakdown расходится с позициями: "
                    + "; ".join(bad) + ". Это ожидаемо: лист посчитан до поправок аналитика.", field="03 Breakdown")
        elif bad:
            iss.add("Ошибка", "Breakdown", f"Разбивка по {label} на листе 03 Breakdown не сходится с позициями: "
                    + "; ".join(bad) + ". В анализе используются веса позиций; лист, вероятно, не обновлён.",
                    field="03 Breakdown")
        else:
            iss.add("Инфо", "Breakdown", f"Разбивка по {label} на листе 03 Breakdown сходится с позициями.")


def _hy_weight(p):
    ig = p["rating"].map(ratings.is_ig)
    return float(p.loc[(ig == False) & (p["instrument_type"] != "cash"), "weight"].sum())  # noqa: E712


def _check_dashboard_claims(p, snap, tol, iss):
    """Числовые заявления в текстовых строках дашборда (Long IG, HY всего, EM всего) против расчёта."""
    long_mask = p["sleeve"].fillna("").str.lower().str.contains("long")
    long_cur = float(p.loc[long_mask, "weight"].sum())
    long_tgt = None
    if snap.allocation is not None and not snap.allocation.empty:
        la = snap.allocation[snap.allocation["sleeve"].str.lower().str.contains("long")]
        long_tgt = float(la["target"].sum()) if not la.empty else None
    em = float(p.loc[p["region"].fillna("").str.upper() == "EM", "weight"].sum())
    for c in snap.dashboard_claims:
        # Тема заявления — по первому слову подписи ("Long IG: ...", "HY всего ...", "EM всего (вкл. EM HY ...)").
        first = re.split(r"[\s:(]+", c["label"].strip().lower())[0]
        nums = [float(x.replace(",", ".")) / 100 for x in re.findall(r"(\d+(?:[.,]\d+)?)\s*%", c["text"])]
        checks = []
        if first == "long" and nums:
            checks.append(("доля Long IG сейчас", nums[0], long_cur))
            if len(nums) > 1 and long_tgt is not None:
                checks.append(("цель Long IG (лист Allocation)", nums[1], long_tgt))
        elif first == "hy" and nums:
            checks.append(("HY всего (рейтинг ниже BBB-)", nums[0], _hy_weight(p)))
        elif first == "em" and nums:
            checks.append(("EM всего (Region = EM)", nums[0], em))
        for what, stated, calc in checks:
            if abs(stated - calc) > tol["weight_pp"] / 100:
                iss.add("Ошибка", "Дашборд", f"Цифра на дашборде не сходится с позициями: {what} — "
                        f"{pct(stated, 1)} в тексте дашборда и {pct(calc, 1)} по позициям. Текст дашборда, вероятно, "
                        "не обновлён; в анализе используются данные по позициям.",
                        field=f"00 Dashboard, строка {c['row']}", file_value=pct(stated, 1), expected=pct(calc, 1))
            else:
                iss.add("Инфо", "Дашборд", f"Цифра на дашборде «{c['label']}» подтверждается расчётом: "
                        f"{what} = {pct(calc, 1)}.")


def _check_dashboard_text(snap, iss):
    """Внутренние противоречия: целевая дюрация в таблице против диапазона в тексте."""
    m = snap.dashboard_metrics.get("mod_duration")
    if not m or m.get("target") is None:
        return
    target = m["target"]
    for text in snap.dashboard_texts:
        for sent in re.split(r"(?<=[.!?])\s+", text):
            s = sent.lower()
            if "дюрац" not in s and "duration" not in s:
                continue
            if "цел" not in s and "target" not in s:
                continue
            for lo, hi in re.findall(r"(\d+[.,]\d+)\s*[–-]\s*(\d+[.,]\d+)", sent):
                lo, hi = float(lo.replace(",", ".")), float(hi.replace(",", "."))
                if not (lo - 0.05 <= target <= hi + 0.05):
                    iss.add("Предупреждение", "Дашборд",
                            f"Целевая дюрация в таблице дашборда ({num(target)}) не попадает в диапазон из "
                            f"текста дашборда ({num(lo, 1)}–{num(hi, 1)}): «{sent.strip().rstrip('.')}». От того, "
                            "какая цель верна, зависит вывод о том, нужно ли удлинять портфель. Предлагаем уточнить "
                            "целевую дюрацию у команды; пока для сравнения используется значение из таблицы.",
                            field="00 Dashboard", file_value=num(target), expected=f"{num(lo, 1)}–{num(hi, 1)}")


def summary(df: pd.DataFrame) -> dict:
    return {k: int((df["Уровень"] == k).sum()) for k in SEVERITY_ORDER}
