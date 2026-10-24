"""Интерфейс Streamlit. Только отображение: все расчёты — в пакете core."""
from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

import ui_analytics
import ui_gspread
import ui_market
import ui_compare
import ui_estimate
import ui_report
from core.pipeline import run as run_pipeline
from core.report import build as build_report
import ui_rules
from core.snapshots import compare as compare_snapshots
from core.snapshots import inherit_targets, order_snapshots
import ui_signals
from core.rules import evaluate as evaluate_rules
from core.gspread import recompute
from core.signals import relative_value, spread_changes
from core.market import curves_between, load_market
import ui_mapping
import ui_methodology
from core.template import build_template
from core.analytics import analyze
from core.config import load_config, merged
from core.loader import LoadError, load_snapshot
from core.quality import run_checks, summary

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


@st.cache_data(ttl=3600, show_spinner="Загружаю данные Treasury и FRED…")
def _market(as_of, _key, key_present: bool):
    return load_market(as_of, _key)


@st.cache_data(ttl=3600, show_spinner=False)
def _curves(as_of, window_days):
    if as_of is None:
        return None
    try:
        return curves_between(as_of - dt.timedelta(days=window_days), as_of)
    except Exception:  # noqa: BLE001 — диагностика необязательна
        return None


@st.cache_data(show_spinner=False)
def _load(data: bytes, name: str, overrides: dict):
    return load_snapshot(data, name=name, overrides=overrides)


def _sidebar(cfg):
    st.sidebar.header("Данные")
    cur = st.sidebar.file_uploader("Текущий снимок портфеля", type=FILE_TYPES, key="cur",
                                   help="Excel (.xlsx, .xlsm, .xls) или CSV. Листы и колонки распознаются "
                                        "по названиям и содержимому.")
    prev = st.sidebar.file_uploader("Предыдущий снимок для сравнения (необязательно)", type=FILE_TYPES, key="prev",
                                    help="Используется для сигналов «аномальное расширение спреда»; полное "
                                         "сравнение снимков — на шаге 7.")
    st.sidebar.download_button("Скачать шаблон файла (.xlsx)", build_template(), file_name="portfolio_template.xlsx",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                               help="Пустой Excel с нужными листами и колонками; требования — во вкладке "
                                    "«Методология и инструкция».")
    sample = None
    samples = ([SAMPLE] if SAMPLE.exists() else []) + sorted((Path(__file__).parent / "samples").glob("*.*"))
    if cur is None and samples:
        choice = st.sidebar.selectbox("Или файл-пример", ["—"] + [p.name for p in samples], index=1,
                                      help="Папка samples/ — тот же портфель в других форматах: CSV, отчёт с "
                                           "разделами и рыночной стоимостью, файл с нестандартной колонкой.")
        sample = next((p for p in samples if p.name == choice), None)
    prev_sample = None
    if prev is None and samples:
        pchoice = st.sidebar.selectbox("Или пример предыдущего снимка", ["—"] + [p.name for p in samples], index=0)
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
        st.caption("Значения по умолчанию и обоснования — в файле config.yaml.")
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
        st.caption("Лимит HY берётся из целей HY-сегментов листа Allocation.")
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
    st.subheader("Методология и инструкция")
    ui_methodology.render(cfg)


def main():
    st.title("Анализ портфеля еврооблигаций")
    data, name, (prev_data, prev_name), cfg = _sidebar(load_config())
    if data is None:
        st.info("Загрузите файл портфеля в панели слева. Требования к файлу и инструкция — ниже.")
        _methodology_only(cfg)
        return
    try:
        snap = _load(data, name, ui_mapping.get_overrides(name))
    except LoadError as e:
        st.error(f"Файл не удалось разобрать: {e}")
        if e.detected.get("labels"):
            if ui_mapping.render(name, e.detected, expanded=True):
                st.rerun()
        _methodology_only(cfg)
        return
    structural = any(n["severity"] != "Инфо" and n["category"] == "Структура" for n in snap.load_notes)
    if ui_mapping.render(name, snap.detected, snap.as_of, expanded=structural or snap.as_of is None):
        _load.clear()
        st.rerun()

    prev_snap, cmp_notes = None, []
    if prev_data:
        try:
            prev_snap = _load(prev_data, prev_name, ui_mapping.get_overrides(prev_name))
        except LoadError as e:
            st.warning(f"Предыдущий снимок «{prev_name}» не удалось разобрать: {e}")
    if prev_snap is not None:
        snap, prev_snap, note = order_snapshots(snap, prev_snap)
        if note:
            cmp_notes.append(note)
            name, prev_name = snap.source_name, prev_snap.source_name
        snap, inh = inherit_targets(snap, prev_snap)
        cmp_notes += inh
    c1, c2, c3 = st.columns(3)
    c1.metric("Дата снимка", snap.as_of.strftime("%d.%m.%Y") if snap.as_of else "не найдена")
    c2.metric("Позиций", len(snap.positions))
    c3.metric("Сумма весов", f"{snap.positions['weight'].sum() * 100:.2f}%")
    st.caption(f"Файл: {name}" + (f" · предыдущий снимок: {prev_name} "
                                  f"({prev_snap.as_of:%d.%m.%Y})" if prev_snap and prev_snap.as_of else ""))

    tab_q, tab_a, tab_mk, tab_g, tab_s, tab_r, tab_c, tab_e, tab_rep, tab_p, tab_m = st.tabs(
        ["1. Качество данных", "2. Аналитика", "3. Рыночные данные", "4. G-spread", "5. Сигналы по спредам",
         "6. Правила добора", "7. Сравнение снимков", "8. Оценка на сегодня и стресс", "9. Отчёт",
         "Позиции (как загружены)", "Методология и инструкция"])

    with tab_m:
        ui_methodology.render(cfg)

    key = _secret("FRED_API_KEY")
    mk = _market(snap.as_of, key, key is not None)
    with tab_mk:
        ui_market.render(mk, snap.dashboard_texts)

    gs = None
    if mk.curve_asof is not None:
        gs = recompute(snap, mk.curve_asof, cfg, _curves(snap.as_of, cfg["gspread"]["implied_date_window_days"]))
    with tab_g:
        if gs is None:
            st.warning("Нет кривой UST на дату снимка — пересчёт G-spread невозможен (подстановок нет).")
        else:
            ui_gspread.render(gs, cfg)

    with tab_s:
        spread = None
        if cfg["signals"]["spread_source"] == "recomputed":
            if gs is None:
                st.warning("Пересчитанный G-spread недоступен — сигналы считаются по G-spread из файла.")
            else:
                spread = pd.Series(gs.table["G (срок)" if gs.primary == "maturity" else "G (дюрация)"].values,
                                   index=snap.positions.index)
        ui_signals.render(relative_value(snap, cfg, spread),
                          spread_changes(snap, prev_snap, cfg) if prev_snap is not None else None, cfg, prev_snap)

    with tab_r:
        ui_rules.render(evaluate_rules(snap, mk, cfg), mk, snap.as_of)

    mk_prev = _market(prev_snap.as_of, key, key is not None) if prev_snap is not None else None
    with tab_c:
        cmp = None
        if prev_snap is not None:
            cmp = compare_snapshots(snap, prev_snap, cfg, mk.curve_asof, mk_prev.curve_asof)
        ui_compare.render(cmp, cmp_notes)

    res = run_pipeline(snap, cfg, prev=prev_snap, market=mk, market_prev=mk_prev, fred_key=key,
                       curves_hist=_curves(snap.as_of, cfg["gspread"]["implied_date_window_days"]))
    res.notes = cmp_notes + res.notes
    with tab_e:
        ui_estimate.render_estimate(res.estimate, mk.errors + [n for n in res.notes if "Оценка" in n])
        st.divider()
        ui_estimate.render_stress(snap, res.stress)

    with tab_rep:
        ui_report.render(build_report(res), snap.as_of.strftime("%Y-%m-%d") if snap.as_of else "portfolio")

    with tab_a:
        ui_analytics.render(analyze(snap, cfg))

    with tab_q:
        issues = run_checks(snap, cfg)
        s = summary(issues)
        k1, k2, k3 = st.columns(3)
        k1.metric("Ошибки", s["Ошибка"])
        k2.metric("Предупреждения", s["Предупреждение"])
        k3.metric("Инфо", s["Инфо"])
        levels = st.multiselect("Показать уровни", list(LEVEL_COLORS), default=["Ошибка", "Предупреждение"])
        view = issues[issues["Уровень"].isin(levels)]
        styled = view.style.apply(
            lambda r: [f"background-color: {LEVEL_COLORS[r['Уровень']]}; color: #1a1a1a"] * len(r), axis=1)
        st.dataframe(styled, hide_index=True, width="stretch",
                     column_config={"Комментарий": st.column_config.TextColumn(width="large")})
        st.download_button("Скачать таблицу проверок (CSV)", issues.to_csv(index=False).encode("utf-8-sig"),
                           file_name="quality_checks.csv", mime="text/csv")

    with tab_p:
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
