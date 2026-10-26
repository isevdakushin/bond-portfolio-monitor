"""Интерфейс Streamlit. Только отображение: весь расчёт — один вызов core.pipeline.run (кэшируется)."""
from __future__ import annotations

import os
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

import ui_analytics
import ui_compare
import ui_corrections
import ui_estimate
import ui_gspread
import ui_mapping
import ui_market
import ui_methodology
import ui_report
import ui_rules
import ui_signals
from core.config import load_config, merged
from core.loader import LoadError, load_snapshot
from core.pipeline import run as run_pipeline
from core.quality import summary
from core.report import build as build_report
from core.snapshots import order_snapshots
from core.template import build_template

st.set_page_config(page_title="Анализ портфеля еврооблигаций", layout="wide")

SAMPLE = Path(__file__).parent / "Main_Portfolio_14_09_26_v2.xlsx"
LEVEL_COLORS = {"Ошибка": "#f8d7da", "Предупреждение": "#fff3cd", "Инфо": "#e7f1ff"}
FILE_TYPES = ["xlsx", "xlsm", "xls", "csv"]
load_dotenv(Path(__file__).parent / ".env")


def _secret(name):
    """Ключ из .env (локально) или из Secrets Streamlit Cloud. Значение нигде не выводится."""
    v = os.environ.get(name)
    if not v:
        try:
            v = st.secrets.get(name)
        except Exception:  # noqa: BLE001 — файла secrets нет
            v = None
    return (v or "").strip() or None


@st.cache_data(show_spinner=False)
def _load(data: bytes, name: str, overrides: dict):
    return load_snapshot(data, name=name, overrides=overrides)


@st.cache_data(ttl=3600, show_spinner="Считаю: проверки, аналитика, рынок Treasury/FRED, сигналы, отчёт…")
def _compute(data, name, ov, prev_data, prev_name, prev_ov, corrections, cfg, _key, key_present: bool):
    """Единый расчёт (тот же, что у скрипта run_report.py). Кэш — по содержимому файлов, ручным правкам,
    поправкам и настройкам; рыночные данные обновляются не чаще раза в час."""
    snap = _load(data, name, ov)
    prev = _load(prev_data, prev_name, prev_ov) if prev_data else None
    res = run_pipeline(snap, cfg, prev=prev, fred_key=_key, corrections=corrections)
    return res, build_report(res)


def _sidebar(cfg):
    st.sidebar.header("Данные")
    cur = st.sidebar.file_uploader("Текущий портфель", type=FILE_TYPES, key="cur",
                                   help="Файл Excel (.xlsx, .xlsm, .xls) или CSV. Листы и колонки "
                                        "определяются по названиям и содержимому, поэтому структура может отличаться "
                                        "от примера.")
    prev = st.sidebar.file_uploader("Предыдущий портфель для сравнения (необязательно)", type=FILE_TYPES, key="prev",
                                    help="Портфель за прошлый период: с ним строится сравнение метрик и "
                                         "позиций и ищутся бумаги с аномальным расширением спреда.")
    st.sidebar.download_button("Скачать шаблон файла (.xlsx)", build_template(), file_name="portfolio_template.xlsx",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                               help="Пустой Excel с листами и колонками, которые читает инструмент. "
                                    "Требования к файлу описаны во вкладке «Методология».")
    sample = None
    samples = ([SAMPLE] if SAMPLE.exists() else []) + sorted((Path(__file__).parent / "samples").glob("*.*"))
    if cur is None and samples:
        choice = st.sidebar.selectbox("Или файл-пример", ["—"] + [p.name for p in samples], index=1,
                                      help="Тот же портфель в других форматах — CSV, отчёт с разделами и "
                                           "рыночной стоимостью, файл с нестандартными колонками. Примеры показывают, "
                                           "что инструмент читает файлы разной структуры.")
        sample = next((p for p in samples if p.name == choice), None)
    prev_sample = None
    if prev is None and samples:
        pchoice = st.sidebar.selectbox("Или пример предыдущего портфеля", ["—"] + [p.name for p in samples], index=0)
        prev_sample = next((p for p in samples if p.name == pchoice), None)

    q = cfg["quality"]
    with st.sidebar.expander("Пороги проверок"):
        ov = {
            "weight_sum_tolerance_pp": st.number_input("Допуск суммы весов, п.п.", 0.0, 5.0,
                                                       float(q["weight_sum_tolerance_pp"]), 0.01),
            "duration_check_abs": st.number_input("Сверка дюрации: допуск, лет", 0.0, 5.0,
                                                  float(q["duration_check_abs"]), 0.1),
            "duration_check_rel": st.number_input("Сверка дюрации: допуск, доля", 0.0, 1.0,
                                                  float(q["duration_check_rel"]), 0.05),
            "duration_over_maturity_error": st.number_input("Дюрация > срока: порог ошибки, лет", 0.0, 2.0,
                                                            float(q["duration_over_maturity_error"]), 0.05),
        }
        st.caption("Значения по умолчанию и их обоснование приведены в методологии (раздел «Допущения и "
                   "пороги»).")
    c = cfg["concentration"]
    with st.sidebar.expander("Лимиты концентрации и допуски"):
        cov = {
            "issuer_max": st.number_input("Эмитент, макс. %", 0.0, 100.0, c["issuer_max"] * 100, 0.5) / 100,
            "country_max": st.number_input("Страна (кроме США), макс. %", 0.0, 100.0, c["country_max"] * 100, 0.5) / 100,
            "sector_group_max": st.number_input("Сектор (укрупнённо), макс. %", 0.0, 100.0,
                                                c["sector_group_max"] * 100, 1.0) / 100,
            "em_total_max": st.number_input("EM всего, макс. %", 0.0, 100.0, c["em_total_max"] * 100, 1.0) / 100,
            "warn_band": st.number_input("«Близко к лимиту» с доли лимита", 0.5, 1.0, float(c["warn_band"]), 0.05),
        }
        aov = {"allocation_gap_pp": st.number_input("Отклонение от целевой аллокации, п.п.", 0.0, 20.0,
                                                    float(cfg["analysis"]["allocation_gap_pp"]), 0.5),
               "fee": st.number_input("Комиссия, % годовых", 0.0, 5.0, cfg["analysis"]["fee"] * 100, 0.05) / 100}
        st.caption("Лимит доли HY берётся как сумма целей HY-сегментов на листе Allocation.")
    sg = cfg["signals"]
    with st.sidebar.expander("Пороги сигналов по спредам"):
        sov = {
            "spread_source": st.selectbox("G-spread для сигналов", ["file", "recomputed"],
                                          index=0 if sg["spread_source"] == "file" else 1,
                                          format_func=lambda x: {"file": "из файла",
                                                                 "recomputed": "пересчитанный (Treasury)"}[x]),
            "wide_abs_bp": st.number_input("«Шире группы»: премия, бп", 0.0, 500.0, float(sg["wide_abs_bp"]), 5.0),
            "wide_rel": st.number_input("«Шире группы»: премия, доля медианы", 0.0, 2.0, float(sg["wide_rel"]), 0.05),
            "duration_window": st.number_input("Окно дюрации пиров, ± лет", 0.5, 10.0,
                                               float(sg["duration_window"]), 0.5),
            "duration_adjust": st.checkbox("Приводить пиров к дюрации бумаги", value=bool(sg["duration_adjust"])),
            "widening_abs_bp": st.number_input("Аномальное расширение сверх группы, бп", 0.0, 500.0,
                                               float(sg["widening_abs_bp"]), 5.0),
        }
    cfg = merged(cfg, {"quality": ov, "concentration": cov, "analysis": aov, "signals": sov})

    prev_data = (prev.getvalue(), prev.name) if prev is not None else \
        (prev_sample.read_bytes(), prev_sample.name) if prev_sample else (None, None)
    if cur is not None:
        return cur.getvalue(), cur.name, prev_data, cfg
    if sample:
        return sample.read_bytes(), sample.name, prev_data, cfg
    return None, None, prev_data, cfg


def _methodology_only(cfg):
    st.divider()
    st.subheader("Методология")
    ui_methodology.render(cfg)


def main():
    st.title("Анализ портфеля еврооблигаций")
    data, name, (prev_data, prev_name), cfg = _sidebar(load_config())
    if data is None:
        has_samples = SAMPLE.exists() or any((Path(__file__).parent / "samples").glob("*.*"))
        st.info("Для начала работы нужно загрузить файл портфеля в панели слева"
                + (" или выбрать файл-пример" if has_samples else "")
                + ". Порядок работы и требования к файлу описаны ниже.")
        _methodology_only(cfg)
        return
    try:
        snap = _load(data, name, ui_mapping.get_overrides(name))
    except LoadError as e:
        st.error(f"Файл не прочитан. {e}")
        if e.detected.get("labels"):
            if ui_mapping.render(name, e.detected, expanded=True):
                st.rerun()
        _methodology_only(cfg)
        return
    structural = any(n["severity"] != "Инфо" and n["category"] == "Структура" for n in snap.load_notes)
    if ui_mapping.render(name, snap.detected, snap.as_of, expanded=structural or snap.as_of is None):
        _load.clear()
        st.rerun()

    sources = {name: data}
    prev_snap = None
    if prev_data:
        sources[prev_name] = prev_data
        try:
            prev_snap = _load(prev_data, prev_name, ui_mapping.get_overrides(prev_name))
        except LoadError as e:
            st.warning(f"Предыдущий портфель «{prev_name}» не прочитан, поэтому сравнение не строится. {e}")
            prev_data = prev_name = None
    # Какой файл текущий (более поздний) — чтобы взять его поправки аналитика.
    cur_name = order_snapshots(snap, prev_snap)[0].source_name if prev_snap is not None else name

    key = _secret("FRED_API_KEY")
    res, report = _compute(data, name, ui_mapping.get_overrides(name), prev_data, prev_name,
                           ui_mapping.get_overrides(prev_name) if prev_name else {}, ui_corrections.get(cur_name),
                           cfg, key, key is not None)
    snap, prev_snap, mk = res.snap, res.prev, res.market
    name = snap.source_name
    prev_name = prev_snap.source_name if prev_snap is not None else None
    corr_log = res.corrections if res.corrections is not None else ui_corrections.empty_log()
    ui_corrections.banner(corr_log)
    c1, c2, c3 = st.columns(3)
    c1.metric("Дата портфеля", snap.as_of.strftime("%d.%m.%Y") if snap.as_of else "не найдена")
    c2.metric("Позиций", len(snap.positions))
    c3.metric("Сумма весов", f"{snap.positions['weight'].sum() * 100:.2f}%")
    st.caption(f"Файл: {name}" + (f" · предыдущий портфель: {prev_name} "
                                  f"({prev_snap.as_of:%d.%m.%Y})" if prev_snap and prev_snap.as_of else ""))

    tab_q, tab_a, tab_mk, tab_g, tab_s, tab_r, tab_c, tab_e, tab_rep, tab_p, tab_m = st.tabs(
        ["1. Качество данных", "2. Аналитика", "3. Рыночные данные", "4. G-spread", "5. Сигналы по спредам",
         "6. Правила добора", "7. Сравнение портфелей", "8. Оценка на сегодня и стресс", "9. Отчёт",
         "Позиции (как загружены)", "Методология"])

    with tab_m:
        ui_methodology.render(cfg)

    with tab_q:
        issues = res.quality
        s = summary(issues)
        k1, k2, k3 = st.columns(3)
        k1.metric("Ошибки", s["Ошибка"])
        k2.metric("Предупреждения", s["Предупреждение"])
        k3.metric("Инфо", s["Инфо"])
        st.caption("Ошибки искажают расчёт, и их стоит исправить — в файле или через «Поправки аналитика» ниже. "
                   "Предупреждения требуют проверки, но на расчёт влияют меньше. Информационные сообщения "
                   "подтверждают, что данные сходятся, или поясняют, как прочитан файл.")
        levels = st.multiselect("Показать уровни", list(LEVEL_COLORS), default=["Ошибка", "Предупреждение"])
        view = issues[issues["Уровень"].isin(levels)]
        box = {"Ошибка": st.error, "Предупреждение": st.warning, "Инфо": st.info}
        for _, r in view.iterrows():
            where = "" if r["Позиция"] == "Портфель" else f"{r['Позиция']} · "
            box[r["Уровень"]](f"**{where}{r['Категория']}.** {r['Комментарий']}")
        with st.expander("Таблица проверок"):
            styled = view.style.apply(
                lambda r: [f"background-color: {LEVEL_COLORS[r['Уровень']]}; color: #1a1a1a"] * len(r), axis=1)
            st.dataframe(styled, hide_index=True, width="stretch",
                         column_config={"Комментарий": st.column_config.TextColumn(width="large")})
        st.download_button("Скачать таблицу проверок (CSV)", issues.to_csv(index=False).encode("utf-8-sig"),
                           file_name="quality_checks.csv", mime="text/csv")
        st.divider()
        ui_corrections.render(res.snap_raw, name, corr_log, sources.get(name, data))

    with tab_a:
        ui_analytics.render(res.analysis)

    with tab_mk:
        ui_market.render(mk, snap.dashboard_texts)

    with tab_g:
        if res.gspread is None:
            st.warning("Кривая UST на дату портфеля не получена, поэтому G-spread не пересчитан. Остальные разделы "
                       "работают по данным файла.")
        else:
            ui_gspread.render(res.gspread, cfg)

    with tab_s:
        if cfg["signals"]["spread_source"] == "recomputed" and res.gspread is None:
            st.warning("Пересчитанного G-spread нет, поэтому сигналы построены по G-spread из файла.")
        ui_signals.render(res.signals, res.spread_changes, cfg, prev_snap)

    with tab_r:
        if res.rules is None:
            st.warning("Рыночные данные не получены, поэтому правила добора не проверены.")
        else:
            ui_rules.render(res.rules, mk, snap.as_of)

    with tab_c:
        ui_compare.render(res.comparison, [n for n in res.notes if not n.startswith("Оценка")])

    with tab_e:
        ui_estimate.render_estimate(res.estimate, (mk.errors if mk else []) + [n for n in res.notes if "Оценка" in n])
        st.divider()
        ui_estimate.render_stress(snap, res.stress)

    with tab_rep:
        ui_report.render(report, snap.as_of.strftime("%Y-%m-%d") if snap.as_of else "portfolio")

    with tab_p:
        if res.corrections is not None and (res.corrections["Статус"] == "применена").any():
            st.caption("Позиции показаны с учётом поправок аналитика; исходные значения приведены в журнале "
                       "поправок на вкладке «1. Качество данных».")
        cols = ["num", "sleeve", "weight", "isin", "issuer", "coupon", "maturity", "name_maturity", "ytm",
                "gspread", "mod_duration", "region", "country", "sector", "rating", "credit_bucket",
                "instrument_type"]
        p = snap.positions[cols].copy()
        for c in ("weight", "coupon", "ytm"):
            p[c] = p[c] * 100
        st.dataframe(p, hide_index=True, width="stretch", column_config={
            "num": "#", "sleeve": "Sleeve", "weight": st.column_config.NumberColumn("Вес, %", format="%.2f"),
            "isin": "ISIN", "issuer": "Бумага", "coupon": st.column_config.NumberColumn("Купон, %", format="%.3f"),
            "maturity": "Погашение (колонка)", "name_maturity": "Погашение (из названия)",
            "ytm": st.column_config.NumberColumn("YTM, %", format="%.3f"),
            "gspread": st.column_config.NumberColumn("G-spread, бп", format="%.0f"),
            "mod_duration": st.column_config.NumberColumn("Mod. dur.", format="%.2f"),
            "region": "Регион", "country": "Страна", "sector": "Сектор", "rating": "Рейтинг",
            "credit_bucket": "Credit bucket", "instrument_type": "Тип"})


main()
