"""Проверки качества данных снимка портфеля.

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
from .loader import Snapshot, map_sleeves, norm

SEVERITY_ORDER = {"Ошибка": 0, "Предупреждение": 1, "Инфо": 2}
COLUMNS = ["Уровень", "Категория", "Позиция", "Поле", "В файле", "Расчёт / ожидание", "Комментарий"]


def pct(x, d=2):
    return "—" if x is None or pd.isna(x) else f"{x * 100:.{d}f}%"


def num(x, d=2):
    return "—" if x is None or pd.isna(x) else f"{x:.{d}f}"


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
        iss.add("Ошибка", "Дата снимка", "Дата снимка не найдена ни в заголовках листов, ни в имени файла. "
                "Укажите её вручную — от неё зависят сроки до погашения и выбор кривой UST.")
        return
    dates = {d for _, d in snap.as_of_candidates}
    srcs = "; ".join(f"{s}: {dfmt(d)}" for s, d in snap.as_of_candidates)
    if len(dates) > 1:
        iss.add("Предупреждение", "Дата снимка", f"Источники даты расходятся ({srcs}). "
                f"Используется {dfmt(snap.as_of)}.", field="Дата", file_value=srcs)
    else:
        iss.add("Инфо", "Дата снимка", f"Дата снимка {dfmt(snap.as_of)} ({srcs}).")


def _check_weights(p, q, iss):
    s = p["weight"].sum(skipna=True)
    tol_pp = q["weight_sum_tolerance_pp"]
    if abs(s - 1) * 100 > tol_pp:
        iss.add("Ошибка", "Веса", f"Сумма весов отличается от 100% больше чем на {tol_pp} п.п.",
                field="Weight", file_value=pct(s, 3), expected="100%")
    else:
        iss.add("Инфо", "Веса", f"Сумма весов {pct(s, 3)} — в пределах допуска ±{tol_pp} п.п.")
    for _, r in p[p["weight"] < 0].iterrows():
        iss.add("Ошибка", "Веса", "Отрицательный вес.", r["label"], "Weight", pct(r["weight"]))


REQUIRED_BY_TYPE = {
    "bond": ["sleeve", "weight", "isin", "coupon", "maturity", "ytm", "gspread", "mod_duration",
             "region", "country", "sector", "rating", "credit_bucket"],
    "fund": ["sleeve", "weight", "isin", "ytm", "gspread", "mod_duration", "region", "rating"],
    "cash": ["sleeve", "weight", "ytm", "mod_duration"],
}
OPTIONAL_INFO = {"fund": ["coupon", "maturity"], "cash": ["isin", "coupon", "maturity", "gspread"]}
TYPE_LABEL = {"bond": "облигация", "fund": "фонд/ETF", "cash": "денежная позиция"}


def _check_fields(p, iss, present=None):
    """Пустые ячейки в колонках, которые в файле есть. Отсутствующие колонки целиком
    уже отмечены при загрузке одним предупреждением."""
    from .loader import COLUMN_LABELS
    for _, r in p.iterrows():
        t = r["instrument_type"]
        empty = [c for c in REQUIRED_BY_TYPE[t] if c in r and (present is None or c in present)
                 and (r[c] is None or pd.isna(r[c]))]
        if empty:
            iss.add("Ошибка", "Пустые поля", f"Не заполнены обязательные поля ({TYPE_LABEL[t]}).",
                    r["label"], ", ".join(COLUMN_LABELS[c] for c in empty))
        expected_empty = [c for c in OPTIONAL_INFO.get(t, []) if (present is None or c in present)
                          and (r[c] is None or pd.isna(r[c]))]
        if expected_empty:
            iss.add("Инфо", "Пустые поля",
                    f"Пусто — для типа «{TYPE_LABEL[t]}» допустимо; пересчёт G-spread для позиции не делается.",
                    r["label"], ", ".join(COLUMN_LABELS[c] for c in expected_empty))


def _check_name_vs_columns(p, snap, q, iss):
    for _, r in p.iterrows():
        nm, cm = r["name_maturity"], r["maturity"]
        if nm is not None and not pd.isna(nm) and cm is not None and not pd.isna(cm) and nm != cm:
            msg = "Дата погашения в названии бумаги не совпадает с колонкой Maturity."
            md_note = _which_date_fits(r, snap, nm, cm)
            iss.add("Ошибка", "Погашение", msg + (" " + md_note if md_note else ""), r["label"], "Maturity",
                    dfmt(cm), f"в названии: {dfmt(nm)}")
        nc, cc = r["name_coupon"], r["coupon"]
        if nc is not None and cc is not None and not pd.isna(nc) and not pd.isna(cc) \
                and abs(nc - cc) > q.get("coupon_tolerance", 0.0001):
            iss.add("Ошибка", "Купон", "Купон в названии бумаги не совпадает с колонкой Coupon.",
                    r["label"], "Coupon", pct(cc, 3), f"в названии: {pct(nc, 3)}")


def _which_date_fits(r, snap, name_date, col_date):
    """Какая дата погашения согласуется с mod. duration из файла (по расчёту)."""
    if not snap.as_of or pd.isna(r["mod_duration"]):
        return ""
    md_col = modified_duration(snap.as_of, col_date, r["coupon"], r["ytm"])
    md_name = modified_duration(snap.as_of, name_date, r["coupon"], r["ytm"])
    if md_col is None or md_name is None:
        return ""
    fits = "названия" if abs(md_name - r["mod_duration"]) < abs(md_col - r["mod_duration"]) else "колонки Maturity"
    return (f"Расчётная mod. duration: {num(md_col)} по дате из колонки, {num(md_name)} по дате из названия; "
            f"в файле {num(r['mod_duration'])} — ближе к дате из {fits}.")


def _check_ratings(p, iss):
    for _, r in p.iterrows():
        rt = r["rating"]
        if rt is None or pd.isna(rt):
            continue
        if ratings.normalize(rt) is None:
            iss.add("Предупреждение", "Рейтинг", "Рейтинг не распознан по шкале S&P/Fitch.", r["label"], "Rating", rt)
            continue
        letter, ig = ratings.letter(rt), ratings.is_ig(rt)
        b = ratings.parse_bucket(r["credit_bucket"])
        bucket = r["credit_bucket"] or ""
        if b["grades"] and letter not in b["grades"]:
            iss.add("Ошибка", "Рейтинг / bucket",
                    f"Рейтинг {rt} не соответствует Credit bucket «{bucket}» "
                    f"(в bucket указаны ступени: {', '.join(sorted(b['grades']))}).",
                    r["label"], "Credit bucket", bucket, f"ступень {letter}")
        elif b["ig"] and not ig:
            iss.add("Ошибка", "Рейтинг / bucket", f"Bucket «{bucket}» — инвестиционный уровень, а рейтинг {rt} — HY.",
                    r["label"], "Credit bucket", bucket, rt)
        elif b["hy"] and ig and not b["fund"]:
            iss.add("Ошибка", "Рейтинг / bucket", f"Bucket «{bucket}» — HY, а рейтинг {rt} — инвестиционный.",
                    r["label"], "Credit bucket", bucket, rt)
        sleeve_tokens = set(re.split(r"[^A-Za-z]+", r["sleeve"] or ""))
        if "IG" in sleeve_tokens and not ig:
            iss.add("Ошибка", "Рейтинг / sleeve", f"Бумага с рейтингом {rt} (HY) находится в IG-сегменте.",
                    r["label"], "Sleeve", r["sleeve"], rt)
        if "HY" in sleeve_tokens and ig:
            iss.add("Предупреждение", "Рейтинг / sleeve", f"Бумага с рейтингом {rt} (IG) находится в HY-сегменте.",
                    r["label"], "Sleeve", r["sleeve"], rt)


def _check_ranges(p, snap, q, iss):
    for _, r in p.iterrows():
        y, g, md, c = r["ytm"], r["gspread"], r["mod_duration"], r["coupon"]
        if pd.notna(y) and not (q["ytm_min"] <= y <= q["ytm_max"]):
            iss.add("Предупреждение", "Странные значения",
                    f"YTM вне диапазона {pct(q['ytm_min'])}–{pct(q['ytm_max'])}.", r["label"], "YTM", pct(y))
        if pd.notna(g) and not (q["gspread_min_bp"] <= g <= q["gspread_max_bp"]):
            iss.add("Предупреждение", "Странные значения",
                    f"G-spread вне диапазона {q['gspread_min_bp']}–{q['gspread_max_bp']} бп.",
                    r["label"], "G-spread", num(g, 0))
        if pd.notna(c) and not (0 <= c <= q["coupon_max"]):
            iss.add("Предупреждение", "Странные значения", "Купон вне правдоподобного диапазона.",
                    r["label"], "Coupon", pct(c))
        if pd.notna(md) and md <= 0:
            iss.add("Ошибка", "Странные значения", "Неположительная дюрация.", r["label"], "Mod. duration", num(md))
        mat = r["maturity"]
        if snap.as_of and mat is not None and not pd.isna(mat):
            if mat <= snap.as_of:
                iss.add("Ошибка", "Погашение", "Дата погашения не позже даты снимка — бумага уже погашена.",
                        r["label"], "Maturity", dfmt(mat), f"> {dfmt(snap.as_of)}")
                continue
            yrs = years_between(snap.as_of, mat)
            if pd.notna(md) and md > yrs + 0.01:
                excess = md - yrs
                big = excess > q["duration_over_maturity_error"]
                iss.add("Ошибка" if big else "Предупреждение", "Дюрация",
                        f"Mod. duration больше срока до погашения на {num(excess)} года — для облигации так не бывает. "
                        + ("" if big else "Вероятно, округление или значение на более раннюю дату."),
                        r["label"], "Mod. duration", num(md), f"≤ {num(yrs)} лет до погашения")
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
                                "Mod. duration из файла заметно отличается от расчётной по купону, YTM и погашению. "
                                "Возможна дюрация к дате call — проверить.",
                                r["label"], "Mod. duration", num(md), f"расчёт: {num(calc)}")


def _check_isin(p, iss):
    for _, r in p.iterrows():
        isin = r["isin"]
        if isin is not None and pd.notna(isin) and not isin_valid(isin):
            iss.add("Предупреждение", "ISIN", "Неверный формат ISIN или контрольная цифра.", r["label"], "ISIN", isin)
    dup = p[p["isin"].notna() & p["isin"].duplicated(keep=False)]
    for isin, g in dup.groupby("isin"):
        iss.add("Ошибка", "ISIN", "ISIN встречается в нескольких строках: " + ", ".join(g["label"]),
                field="ISIN", file_value=isin)


def _check_countries(p, q, iss):
    bad = {norm(x) for x in q["non_country_values"]}
    for _, r in p.iterrows():
        if r["country"] and norm(r["country"]) in bad:
            iss.add("Предупреждение", "Страна",
                    "В колонке Country указан регион/агрегат, а не страна — позиция не попадёт "
                    "в корректную концентрацию по странам.", r["label"], "Country", r["country"])


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
    after_fix = " Ожидаемо: итог файла посчитан до поправок аналитика."
    t = snap.file_totals
    if t:
        for k, (label, tl, f) in spec.items():
            v = t.get(k)
            if v is None:
                continue
            if abs(v - calc[k]) > tl:
                if k in corrected:
                    iss.add("Инфо", "Итоги листа портфеля", f"{label}: итог в файле отличается от расчёта.{after_fix}",
                            field=f"строка {t['excel_row']}", file_value=f(v), expected=f(calc[k]))
                    continue
                iss.add("Ошибка", "Итоги листа портфеля", f"{label}: итог в файле не совпадает с расчётом по позициям.",
                        field=f"строка {t['excel_row']}", file_value=f(v), expected=f(calc[k]))
            else:
                iss.add("Инфо", "Итоги листа портфеля", f"{label}: итог в файле совпадает с расчётом ({f(calc[k])}).")
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
                iss.add("Инфо", "Дашборд", f"«{m[k]['label']}»: значение на дашборде отличается от расчёта.{after_fix}",
                        field=f"00 Dashboard, строка {m[k]['row']}", file_value=dfmtf[k](v), expected=dfmtf[k](calc_v))
                continue
            iss.add("Ошибка", "Дашборд", f"«{m[k]['label']}»: текущее значение на дашборде не совпадает с расчётом.",
                    field=f"00 Dashboard, строка {m[k]['row']}", file_value=dfmtf[k](v), expected=dfmtf[k](calc_v))
    if m.get("ytm_gross") and m.get("ytm_net") and m["ytm_gross"].get("target") and m["ytm_net"].get("target"):
        implied = m["ytm_gross"]["target"] - m["ytm_net"]["target"]
        if abs(implied - fee) > tol["ytm"]:
            iss.add("Предупреждение", "Дашборд", "Разница целевых YTM брутто и нетто не равна комиссии из настроек.",
                    field="00 Dashboard", file_value=pct(implied), expected=pct(fee))


def _check_allocation(p, snap, tol, iss):
    a = snap.allocation
    if a is None or a.empty:
        return
    tsum = a["target"].sum(skipna=True)
    if abs(tsum - 1) > tol["weight_pp"] / 100:
        iss.add("Ошибка", "Аллокация", "Сумма целевой аллокации не равна 100%.", field="Target",
                file_value=pct(tsum), expected="100%")
    by_sleeve = p.groupby("sleeve")["weight"].sum()
    for _, r in a.iterrows():
        if r["match"] == "частичное":
            iss.add("Инфо", "Аллокация", f"Sleeve «{r['sleeve']}» листа Allocation сопоставлен с «{r['portfolio_sleeve']}» "
                    "листа портфеля по частичному совпадению названия.", r["sleeve"], "Sleeve")
        elif r["match"] in ("не найдено", "неоднозначное"):
            iss.add("Ошибка", "Аллокация", f"Sleeve «{r['sleeve']}» не удалось сопоставить с портфелем ({r['match']}).",
                    r["sleeve"], "Sleeve")
            continue
        calc = by_sleeve.get(r["portfolio_sleeve"], 0.0)
        if r["current"] is not None and pd.notna(r["current"]) and abs(r["current"] - calc) > tol["weight_pp"] / 100:
            if {"weight", "sleeve"} & (getattr(snap, "corrected_fields", set()) or set()):
                iss.add("Инфо", "Аллокация", "Текущая доля на листе Allocation отличается от суммы весов позиций. "
                        "Ожидаемо: лист посчитан до поправок аналитика.", r["sleeve"], "Current", pct(r["current"]), pct(calc))
            else:
                iss.add("Ошибка", "Аллокация", "Текущая доля на листе Allocation не совпадает с суммой весов позиций.",
                        r["sleeve"], "Current", pct(r["current"]), pct(calc))
        if r["gap"] is not None and pd.notna(r["gap"]) and r["current"] is not None and pd.notna(r["current"]):
            if abs(r["gap"] - (r["target"] - r["current"])) > 1e-6:
                iss.add("Предупреждение", "Аллокация", "Gap to target ≠ Target − Current.", r["sleeve"], "Gap",
                        pct(r["gap"]), pct(r["target"] - r["current"]))
    mapped = set(a["portfolio_sleeve"].dropna())
    for s in by_sleeve.index:
        if s not in mapped:
            iss.add("Ошибка", "Аллокация", f"Sleeve «{s}» есть в портфеле ({pct(by_sleeve[s])}), "
                    "но отсутствует на листе Allocation — для него нет цели.", s, "Sleeve")


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
                bad.append(f"{name}: {pct(fv)} в файле vs {pct(cv)} по позициям")
        if bad and {col, "weight"} & (getattr(snap, "corrected_fields", set()) or set()):
            iss.add("Инфо", "Breakdown", f"Распределение по {label} на листе Breakdown отличается от позиций "
                    "(ожидаемо: лист посчитан до поправок аналитика): " + "; ".join(bad), field="03 Breakdown")
        elif bad:
            iss.add("Ошибка", "Breakdown", f"Распределение по {label} на листе Breakdown расходится с позициями: "
                    + "; ".join(bad), field="03 Breakdown")
        else:
            iss.add("Инфо", "Breakdown", f"Распределение по {label} на листе Breakdown совпадает с расчётом по позициям.")


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
                iss.add("Ошибка", "Дашборд", f"Заявление на дашборде не совпадает с расчётом: {what}.",
                        field=f"00 Dashboard, строка {c['row']}", file_value=pct(stated, 1), expected=pct(calc, 1))
            else:
                iss.add("Инфо", "Дашборд", f"Заявление «{c['label']}» подтверждается расчётом: {what} = {pct(calc, 1)}.")


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
                            f"Целевая mod. duration в таблице ({num(target)}) не попадает в диапазон из текста "
                            f"дашборда ({num(lo, 1)}–{num(hi, 1)}): «{sent.strip()}».",
                            field="00 Dashboard", file_value=num(target), expected=f"{num(lo, 1)}–{num(hi, 1)}")


def summary(df: pd.DataFrame) -> dict:
    return {k: int((df["Уровень"] == k).sum()) for k in SEVERITY_ORDER}
