"""Вкладка «Аналитика» интерфейса Streamlit (только отображение результатов core.analytics)."""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core import narrative as N
from core.analytics import Analysis

# Категориальные цвета (проверенная палитра): 1 — синий, 2 — оранжевый.
C1, C2 = "#2a78d6", "#eb6834"
STATUS_BG = {"превышен": "#f8d7da", "перевес": "#fff3cd", "недовес": "#fff3cd", "близко к лимиту": "#fff3cd",
             "ниже цели": "#fff3cd", "выше цели": "#fff3cd"}


def _bp(x):
    return f"{x * 10000:+.0f} бп" if x is not None and pd.notna(x) else "—"


def _style_status(df, col="Статус"):
    def f(r):
        bg = STATUS_BG.get(r[col])
        return [f"background-color: {bg}; color: #1a1a1a" if bg else ""] * len(r)
    return df.style.apply(f, axis=1)


def _pct(df, cols):
    df = df.copy()
    for c in cols:
        if c in df:
            df[c] = df[c] * 100
    return df


def _bar_layout(fig, height):
    fig.update_layout(height=height, margin=dict(l=10, r=10, t=30, b=10), bargap=0.25, bargroupgap=0.08,
                      legend=dict(orientation="h", yanchor="bottom", y=1.0, x=0),
                      xaxis=dict(ticksuffix="%", gridcolor="rgba(137,135,129,0.25)", zeroline=False),
                      yaxis=dict(autorange="reversed"), paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
    return fig


def render(an: Analysis):
    m, t = an.metrics, an.targets.set_index("key")

    st.subheader("Ключевые метрики против цели")
    k = st.columns(4)
    k[0].metric("YTM брутто", f"{m['ytm_gross'] * 100:.2f}%", _bp(t.loc["ytm_gross", "Отклонение"]) + " к цели",
                delta_color="off")
    k[1].metric(f"YTM после комиссии {m['fee'] * 100:.1f}%", f"{m['ytm_net'] * 100:.2f}%",
                _bp(t.loc["ytm_net", "Отклонение"]) + " к цели", delta_color="off")
    gd = t.loc["mod_duration", "Отклонение"]
    k[2].metric("Mod. duration", f"{m['mod_duration']:.2f}", f"{gd:+.2f} к цели" if pd.notna(gd) else None,
                delta_color="off")
    gg = t.loc["gspread", "Отклонение"]
    k[3].metric("G-spread", f"{m['gspread']:.0f} бп", f"{gg:+.0f} бп к цели" if pd.notna(gg) else None,
                delta_color="off")
    k2 = st.columns(4)
    k2[0].metric("Средний рейтинг", m["avg_rating"] or "—", help="Каждой ступени шкалы присвоен балл (AAA = 1, …, "
                 "BBB- = 10); баллы взвешены по долям позиций и округлены до ближайшей ступени.")
    k2[1].metric("IG / HY / кэш, %", f"{m['ig_share'] * 100:.0f}/{m['hy_share'] * 100:.0f}/"
                 f"{m['cash_share'] * 100:.0f}")
    k2[2].metric("EM", f"{m['em_share'] * 100:.1f}%")
    k2[3].metric("Эмитентов (эффективно)", f"{m['n_issuers']} ({m['effective_issuers']:.0f})",
                 help="В скобках — эффективное число эмитентов: 1 / индекс Херфиндаля по их долям (без "
                      "денежных позиций). Чем оно меньше общего числа эмитентов, тем сильнее портфель "
                      "сконцентрирован в крупных позициях.")
    txt = N.targets(an.targets, m, an.proforma)
    if txt:
        st.markdown(txt)

    tt = an.targets.drop(columns="key").copy()
    for c in ("Текущее", "Цель", "Отклонение", "Pro forma при целевой аллокации"):
        if c in tt:
            tt[c] = [(f"{v * 100:.2f}%" if i < 2 else f"{v:.2f}") if pd.notna(v) else "—"
                     for i, v in enumerate(tt[c])]
    tt["Допуск"] = [f"±{v * 10000:.0f} бп" if i < 2 else (f"±{v:.2f}" if i == 2 else f"±{v:.0f} бп")
                    for i, v in enumerate(tt["Допуск"])]
    st.dataframe(_style_status(tt), hide_index=True, width="stretch")
    for n in an.notes:
        st.caption(n)

    if an.allocation is not None:
        st.subheader("Аллокация по сегментам: текущая и целевая")
        a = an.allocation
        fig = go.Figure()
        fig.add_bar(y=a["Sleeve"], x=a["Текущее"] * 100, name="Текущая", orientation="h", marker_color=C1,
                    marker_line_width=0, hovertemplate="%{y}<br>Текущая: %{x:.1f}%<extra></extra>")
        fig.add_bar(y=a["Sleeve"], x=a["Цель"] * 100, name="Целевая", orientation="h", marker_color=C2,
                    marker_line_width=0, hovertemplate="%{y}<br>Целевая: %{x:.1f}%<extra></extra>")
        st.plotly_chart(_bar_layout(fig, 330), width="stretch", theme="streamlit")
        st.dataframe(_style_status(_pct(a, ["Цель", "Текущее", "Отклонение"])), hide_index=True, width="stretch",
                     column_config={c: st.column_config.NumberColumn(c + ", %", format="%.1f")
                                    for c in ("Цель", "Текущее")} |
                     {"Отклонение": st.column_config.NumberColumn("Отклонение, п.п.", format="%+.1f")})
        txt = N.allocation(a)
        if txt:
            st.markdown(txt)

    st.subheader("Концентрации и лимиты")
    c = an.concentration
    flagged = c[c["Статус"] != "в пределах"]
    ccfg = {"Доля": st.column_config.NumberColumn("Доля, %", format="%.1f"),
            "Лимит": st.column_config.NumberColumn("Лимит, %", format="%.1f")}
    if flagged.empty:
        st.success("Все лимиты концентрации соблюдены: ни эмитенты, ни страны, ни секторы, ни доли EM и HY не "
                   "превышают лимиты и не приближаются к ним.")
    for _, r in flagged.iterrows():
        (st.error if r["Статус"] == "превышен" else st.warning)(N.concentration(r))
    with st.expander("Все проверки лимитов"):
        st.dataframe(_pct(c, ["Доля", "Лимит"]), hide_index=True, width="stretch", column_config=ccfg)

    st.subheader("Распределение портфеля")
    dim = st.selectbox("Измерение", list(an.breakdowns), index=0)
    b = an.breakdowns[dim]
    label = b.columns[0]
    fig = go.Figure()
    fig.add_bar(y=b[label].astype(str), x=b["Вес"] * 100, orientation="h", marker_color=C1, marker_line_width=0,
                text=[f"{v * 100:.1f}%" for v in b["Вес"]], textposition="outside", cliponaxis=False,
                customdata=b[["YTM", "Mod. duration", "G-spread, бп"]].fillna(0).values,
                hovertemplate="%{y}<br>Вес: %{x:.1f}%<br>YTM: %{customdata[0]:.2%}<br>"
                              "Mod. dur.: %{customdata[1]:.2f}<br>G-spread: %{customdata[2]:.0f} бп<extra></extra>")
    st.plotly_chart(_bar_layout(fig, max(220, 28 * len(b) + 60)), width="stretch", theme="streamlit")
    st.dataframe(_pct(b, ["Вес", "YTM", "Вклад в YTM", "Доля DTS"]), hide_index=True, width="stretch",
                 column_config={
                     "Вес": st.column_config.NumberColumn("Вес, %", format="%.1f"),
                     "YTM": st.column_config.NumberColumn("YTM, %", format="%.2f"),
                     "Mod. duration": st.column_config.NumberColumn(format="%.2f"),
                     "G-spread, бп": st.column_config.NumberColumn(format="%.0f"),
                     "Вклад в YTM": st.column_config.NumberColumn("Вклад в YTM, п.п.", format="%.2f"),
                     "Вклад в дюрацию": st.column_config.NumberColumn(format="%.2f"),
                     "Доля DTS": st.column_config.NumberColumn("Доля кредитного риска (DTS), %", format="%.1f",
                                                               help="DTS — модифицированная дюрация × G-spread. "
                                                                    "Показывает, какую долю чувствительности портфеля "
                                                                    "к изменению спредов даёт группа."),
                 })

    st.subheader("Позиции: вклад в метрики и кредитный риск")
    p = an.positions.sort_values("dts_contrib", ascending=False)
    pv = pd.DataFrame({
        "Позиция": p["label"], "Sleeve": p["sleeve"], "Рейтинг": p["rating"], "Вес, %": p["weight"] * 100,
        "YTM, %": p["ytm"] * 100, "Mod. dur.": p["mod_duration"], "G-spread, бп": p["gspread"],
        "Вклад в YTM, п.п.": p["ytm_contrib"] * 100, "Вклад в дюрацию": p["md_contrib"],
        "Доля DTS, %": p["dts_contrib"] / p["dts_contrib"].sum() * 100})
    st.dataframe(pv, hide_index=True, width="stretch", column_config={
        c: st.column_config.NumberColumn(format="%.2f") for c in pv.columns if pv[c].dtype.kind == "f"})
