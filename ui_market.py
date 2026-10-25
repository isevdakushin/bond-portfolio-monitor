"""Вкладка «Рыночные данные»: кривая UST и HY OAS с датами и источниками."""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core.market import SOURCE_HY, SOURCE_UST, Market, check_file_claims

C1, C2 = "#2a78d6", "#eb6834"
TENOR_LABELS = {1 / 12: "1M", 0.125: "1.5M", 2 / 12: "2M", 0.25: "3M", 4 / 12: "4M", 0.5: "6M", 1: "1Y", 2: "2Y",
                3: "3Y", 5: "5Y", 7: "7Y", 10: "10Y", 20: "20Y", 30: "30Y"}


def _lab(t):
    for k, v in TENOR_LABELS.items():
        if abs(k - t) < 1e-6:
            return v
    return f"{t:g}Y"


def _d(d):
    return d.strftime("%d.%m.%Y") if d else "—"


def _layout(fig, height, yfmt="%", xlog=False):
    fig.update_layout(height=height, margin=dict(l=10, r=10, t=30, b=10), hovermode="x unified",
                      legend=dict(orientation="h", yanchor="bottom", y=1.0, x=0),
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      yaxis=dict(gridcolor="rgba(137,135,129,0.25)", ticksuffix=yfmt, zeroline=False),
                      xaxis=dict(gridcolor="rgba(137,135,129,0.15)"))
    if xlog:
        fig.update_xaxes(type="log")
    return fig


def _source_line(label, url, obs_date, fetched_at, how, extra=""):
    when = fetched_at.strftime("%d.%m.%Y %H:%M") if fetched_at else "—"
    st.caption(f"**{label}** · наблюдение на {_d(obs_date)} · [ссылка на источник]({url}) · "
               f"загружено {when} ({how}){extra}")


def render(m: Market, dashboard_texts):
    if m.errors:
        for e in m.errors:
            st.warning(e + " Значение не показано.")
    ca, cl = m.curve_asof, m.curve_latest

    st.subheader("Ключевые уровни")
    k = st.columns(4)
    if ca:
        k[0].metric(f"10Y UST на дату портфеля ({_d(ca.date)})", f"{ca.at(10) * 100:.2f}%")
    if cl:
        delta = f"{(cl.at(10) - ca.at(10)) * 10000:+.0f} бп с даты портфеля" if ca else None
        k[1].metric(f"10Y UST сейчас ({_d(cl.date)})", f"{cl.at(10) * 100:.2f}%", delta, delta_color="off")
    if m.hy_asof and m.hy_asof[0]:
        k[2].metric(f"HY OAS на дату портфеля ({_d(m.hy_asof[0])})", f"{m.hy_asof[1]:.0f} бп")
    if m.hy_latest and m.hy_latest[0]:
        delta = f"{m.hy_latest[1] - m.hy_asof[1]:+.0f} бп с даты портфеля" if m.hy_asof and m.hy_asof[0] else None
        k[3].metric(f"HY OAS сейчас ({_d(m.hy_latest[0])})", f"{m.hy_latest[1]:.0f} бп", delta, delta_color="off")

    st.markdown("**Источники**")
    if ca:
        _source_line(SOURCE_UST, ca.url, ca.date, ca.fetched_at, ca.how,
                     "" if ca.date == ca.requested else f" · на {_d(ca.requested)} данных нет (выходной) — "
                                                          "взят предыдущий рабочий день")
    if cl and cl.date != (ca.date if ca else None):
        _source_line(SOURCE_UST + " (последняя)", cl.url, cl.date, cl.fetched_at, cl.how)
    if m.hy_meta:
        _source_line(SOURCE_HY, m.hy_meta["url"], m.hy_latest[0] if m.hy_latest else None,
                     m.hy_meta.get("fetched_at"), m.hy_meta.get("how"), f" · канал: {m.hy_meta.get('channel')}")

    claims = check_file_claims(dashboard_texts, m)
    if claims:
        st.subheader("Сверка рыночных цифр из файла")
        rows = [{"Что в файле": c["context"], "В файле": f"{c['stated'] * 100:.2f}%",
                 f"Treasury на {_d(c['date'])}": f"{c['official'] * 100:.2f}%",
                 "Расхождение": f"{c['diff_bp']:+.0f} бп", "Статус": "совпадает" if c["ok"] else "расходится"}
                for c in claims]
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        st.caption("Допуск 1 бп. Расхождение обычно значит, что в файле уровень на другое время дня "
                   "(Treasury публикует закрытие).")

    if ca:
        st.subheader("Кривая доходности UST (par yield)")
        fig = go.Figure()
        fig.add_scatter(x=ca.tenors, y=[y * 100 for y in ca.yields], mode="lines+markers", name=f"{_d(ca.date)}",
                        line=dict(color=C1, width=2), marker=dict(size=8),
                        customdata=[_lab(t) for t in ca.tenors], hovertemplate="%{customdata}: %{y:.2f}%")
        if cl and cl.date != ca.date:
            fig.add_scatter(x=cl.tenors, y=[y * 100 for y in cl.yields], mode="lines+markers", name=f"{_d(cl.date)}",
                            line=dict(color=C2, width=2), marker=dict(size=8),
                            customdata=[_lab(t) for t in cl.tenors], hovertemplate="%{customdata}: %{y:.2f}%")
        _layout(fig, 340, xlog=True)
        fig.update_xaxes(tickvals=ca.tenors, ticktext=[_lab(t) for t in ca.tenors], title="срок (логарифмическая шкала)")
        st.plotly_chart(fig, width="stretch", theme="streamlit")
        tbl = pd.DataFrame({"Срок": [_lab(t) for t in ca.tenors], f"{_d(ca.date)}, %": [y * 100 for y in ca.yields]})
        if cl:
            tbl[f"{_d(cl.date)}, %"] = [cl.at(t) * 100 for t in ca.tenors]
            tbl["Изменение, бп"] = [(cl.at(t) - y) * 10000 for t, y in zip(ca.tenors, ca.yields)]
        with st.expander("Таблица кривой"):
            st.dataframe(tbl, hide_index=True, width="stretch", column_config={
                c: st.column_config.NumberColumn(format="%.2f" if "%" in c else "%+.0f") for c in tbl.columns[1:]})

    c1, c2 = st.columns(2)
    if m.ust10_series is not None and not m.ust10_series.empty:
        with c1:
            st.subheader("10Y UST, %")
            s = m.ust10_series
            fig = go.Figure()
            fig.add_scatter(x=list(s.index), y=s.values * 100, mode="lines", name="10Y UST",
                            line=dict(color=C1, width=2), hovertemplate="%{x|%d.%m.%Y}: %{y:.2f}%<extra></extra>")
            if m.as_of:
                fig.add_vline(x=m.as_of, line=dict(color="#898781", width=1, dash="dot"))
            st.plotly_chart(_layout(fig, 300), width="stretch", theme="streamlit")
            st.caption(f"Пунктир — дата портфеля. Период {_d(s.index.min())}–{_d(s.index.max())}. "
                       f"Максимум: {s.max() * 100:.2f}% ({_d(s.idxmax())}); "
                       f"минимум: {s.min() * 100:.2f}% ({_d(s.idxmin())}).")
    if m.hy_series is not None and not m.hy_series.empty:
        with c2:
            st.subheader("HY OAS, бп")
            s = m.hy_series[m.hy_series.index >= (m.ust10_series.index.min() if m.ust10_series is not None
                                                   and not m.ust10_series.empty else m.hy_series.index.min())]
            fig = go.Figure()
            fig.add_scatter(x=list(s.index), y=s.values, mode="lines", name="HY OAS",
                            line=dict(color=C1, width=2), hovertemplate="%{x|%d.%m.%Y}: %{y:.0f} бп<extra></extra>")
            if m.as_of:
                fig.add_vline(x=m.as_of, line=dict(color="#898781", width=1, dash="dot"))
            st.plotly_chart(_layout(fig, 300, yfmt=""), width="stretch", theme="streamlit")
            st.caption(f"Пунктир — дата портфеля. Период {_d(s.index.min())}–{_d(s.index.max())}. "
                       f"Максимум: {s.max():.0f} бп ({_d(s.idxmax())}); "
                       f"минимум: {s.min():.0f} бп ({_d(s.idxmin())}).")
