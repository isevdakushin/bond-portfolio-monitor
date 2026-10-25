"""Вкладка «Правила добора»: статус правил дашборда по рыночным уровням и действия по стратегии."""
from __future__ import annotations

import datetime as dt

import plotly.graph_objects as go
import streamlit as st

C1 = "#2a78d6"
ZONE = "rgba(235,104,52,0.15)"
LINE = "#eb6834"
STATUS_BG = {"сработало сейчас": "#fde2d6", "уровень достигался, сейчас ниже": "#fff3cd"}
PRIORITY = {1: "по правилу", 2: "на проверку", 3: "к сведению"}


def _chart(series, as_of, height, ysuffix, hover):
    fig = go.Figure()
    fig.add_scatter(x=list(series.index), y=series.values, mode="lines", line=dict(color=C1, width=2),
                    name="", hovertemplate=hover)
    if as_of:
        fig.add_vline(x=as_of, line=dict(color="#898781", width=1, dash="dot"))
    fig.update_layout(height=height, margin=dict(l=10, r=10, t=10, b=10), showlegend=False,
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      yaxis=dict(gridcolor="rgba(137,135,129,0.25)", ticksuffix=ysuffix, zeroline=False))
    return fig


def render(ev: dict, market, as_of):
    rules = ev["rules"]
    st.caption("Правила — из файла компании (цитаты ниже), уровни — Treasury и FRED. Шаг лестницы считается "
               "достигнутым, если 10Y UST не ниже нижней границы зоны. **Решение — за аналитиком.**")

    st.subheader("Действия по стратегии компании")
    if not ev["actions"]:
        st.success("По правилам дашборда действий нет.")
    for a in ev["actions"]:
        box = st.error if a["Приоритет"] == 1 else st.warning if a["Приоритет"] == 2 else st.info
        box(f"**{a['Действие']}** · {PRIORITY[a['Приоритет']]}  \nОснование: {a['Основание']}.  \n"
            f"Финансирование — {a['Финансирование']}.")

    st.subheader("Статус правил")
    t = ev["table"].drop(columns=["Источник правила"])
    st.dataframe(t.style.apply(lambda r: [f"background-color: {STATUS_BG[r['Статус']]}; color: #1a1a1a"
                                          if r["Статус"] in STATUS_BG else ""] * len(r), axis=1),
                 hide_index=True, width="stretch")
    k = st.columns(3)
    k[0].metric("Long IG сейчас (в файле)", f"{ev['long_now'] * 100:.1f}%")
    if ev["hy_now"] is not None:
        k[1].metric(f"{rules.hy_instrument} сейчас (в файле)", f"{ev['hy_now'] * 100:.1f}%")
    if ev["next_reviews"]:
        d = ev["next_reviews"][0]
        k[2].metric("Ближайший календарный пересмотр", f"{d:%d.%m.%Y}", f"через {(d - ev['today']).days} дн.",
                    delta_color="off")

    with st.expander("Откуда взяты правила"):
        for key, label in (("ladder", "Лестница добора длины"), ("hy", "Триггер HY OAS"), ("calendar", "Календарь")):
            st.markdown(f"- **{label}:** {rules.source.get(key, '—')}")

    start = (as_of or ev["today"]) - dt.timedelta(days=60)
    c1, c2 = st.columns(2)
    s = market.ust10_series
    if s is not None and not s.empty:
        with c1:
            st.markdown("**10Y UST и зоны лестницы**")
            fig = _chart(s[s.index >= start] * 100, as_of, 300, "%", "%{x|%d.%m.%Y}: %{y:.2f}%<extra></extra>")
            for i, z in enumerate(rules.ladder, start=1):
                fig.add_hrect(y0=z["lower"] * 100, y1=z["upper"] * 100, fillcolor=ZONE, line_width=0,
                              annotation_text=f"шаг {i}: {z['lower'] * 100:.2f}–{z['upper'] * 100:.2f}%",
                              annotation_position="top left", annotation_font_size=11)
            st.plotly_chart(fig, width="stretch", theme="streamlit")
    h = market.hy_series
    if h is not None and not h.empty and rules.hy_trigger_bp:
        with c2:
            st.markdown("**HY OAS и триггер**")
            fig = _chart(h[h.index >= start], as_of, 300, "", "%{x|%d.%m.%Y}: %{y:.0f} бп<extra></extra>")
            fig.add_hline(y=rules.hy_trigger_bp, line=dict(color=LINE, width=2, dash="dash"),
                          annotation_text=f"триггер {rules.hy_trigger_bp:.0f} бп", annotation_position="top left",
                          annotation_font_size=11)
            st.plotly_chart(fig, width="stretch", theme="streamlit")
    st.caption("Пунктир по вертикали — дата снимка. Период — 60 дней до снимка и до последней даты данных.")
