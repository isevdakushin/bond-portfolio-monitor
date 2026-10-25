"""Вкладка «Сравнение портфелей»."""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core import narrative as N
from core.snapshots import Comparison

C1, C2, C3 = "#2a78d6", "#eb6834", "#1baf7a"
POS, NEG = "#2a78d6", "#e34948"
ROW_BG = {"новая": "#dbe9fb", "выбыла": "#f0efec"}


def _d(d):
    return d.strftime("%d.%m.%Y") if d else "—"


def _fmt(v, kind):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    if kind == "txt":
        return str(v)
    if kind == "pct":
        return f"{v * 100:.2f}%"
    if kind == "share":
        return f"{v * 100:.1f}%"
    if kind == "num":
        return f"{v:.2f}"
    return f"{v:.0f}"


def _delta(v, kind):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    if kind == "pct":
        return f"{v * 10000:+.0f} бп"
    if kind == "share":
        return f"{v * 100:+.1f} п.п."
    if kind == "num":
        return f"{v:+.2f}"
    if kind == "bp":
        return f"{v:+.0f} бп"
    if kind == "txt":
        return f"{v:+.2f} ступени шкалы ({'хуже' if v > 0 else 'лучше' if v < 0 else 'без изменений'})"
    return f"{v:+.0f}"


def render(cmp: Comparison | None, notes: list):
    if cmp is None:
        st.info("Для сравнения нужен предыдущий портфель — его можно загрузить в панели слева. Тогда здесь "
                "появятся изменения метрик, аллокации, позиций и рейтингов, а также разложение изменения доходности "
                "портфеля на рыночные и собственные факторы.")
        return
    for n in notes + cmp.notes:
        st.info(n)
    pos = cmp.positions
    n_new, n_rem = int((pos["Статус"] == "новая").sum()), int((pos["Статус"] == "выбыла").sum())
    st.markdown(N.comparison(cmp))
    st.caption(f"Портфель на {_d(cmp.cur_date)} сравнивается с портфелем на {_d(cmp.prev_date)}. Бумаги "
               f"сопоставлены по ISIN, а если его нет — по названию или по эмитенту и дате погашения. В портфеле "
               f"осталось {int((pos['Статус'] == 'осталась').sum())} бумаг, добавлено {n_new}, выбыло {n_rem}.")

    mt = cmp.metrics.set_index("key")
    k = st.columns(4)
    for col, key, label in zip(k, ("ytm_gross", "mod_duration", "gspread", "n_positions"),
                               ("YTM брутто", "Mod. duration", "G-spread, бп", "Позиций")):
        r = mt.loc[key]
        col.metric(label, _fmt(r["Сейчас"], r["Тип"]), f"{_delta(r['Изменение'], r['Тип'])} к {_d(cmp.prev_date)}",
                   delta_color="off")

    st.subheader("Метрики портфеля")
    view = pd.DataFrame({"Метрика": mt["Метрика"],
                         _d(cmp.prev_date): [_fmt(v, t) for v, t in zip(mt["Ранее"], mt["Тип"])],
                         _d(cmp.cur_date): [_fmt(v, t) for v, t in zip(mt["Сейчас"], mt["Тип"])],
                         "Изменение": [_delta(v, t) for v, t in zip(mt["Изменение"], mt["Тип"])],
                         "Цель (сейчас)": [_fmt(v, t) if "Цель (сейчас)" in mt and pd.notna(v) else ""
                                           for v, t in zip(mt.get("Цель (сейчас)", pd.Series([None] * len(mt))),
                                                           mt["Тип"])]})
    st.dataframe(view, hide_index=True, width="stretch")

    st.subheader("Откуда изменение доходности портфеля")
    a = cmp.attribution["YTM, бп"]
    comp = a[a["Компонент"] != "Итого изменение портфеля"]
    total = float(a.loc[a["Компонент"] == "Итого изменение портфеля", "Вклад"].iloc[0])
    fig = go.Figure()
    fig.add_bar(y=comp["Компонент"], x=comp["Вклад"], orientation="h",
                marker_color=[POS if v >= 0 else NEG for v in comp["Вклад"]], marker_line_width=0,
                text=[f"{v:+.1f} бп" for v in comp["Вклад"]], textposition="outside", cliponaxis=False,
                hovertemplate="%{y}: %{x:+.1f} бп<extra></extra>")
    fig.add_vline(x=0, line=dict(color="#c3c2b7", width=1))
    fig.update_layout(height=60 * len(comp) + 60, margin=dict(l=10, r=40, t=10, b=10), showlegend=False,
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      xaxis=dict(ticksuffix=" бп", gridcolor="rgba(137,135,129,0.25)", zeroline=False),
                      yaxis=dict(autorange="reversed"))
    st.plotly_chart(fig, width="stretch", theme="streamlit")
    tot = f"{total:+.1f}".replace(".", ",")
    st.caption(f"Доходность портфеля изменилась на {tot} б.п.; это сумма вкладов на графике. «Рынок UST» — "
               "сдвиг кривой Treasury между датами портфелей на срок каждой бумаги, взвешенный по долям прошлого "
               "портфеля; остальные компоненты отражают изменения спредов и состава портфеля.")
    with st.expander("Разложение G-spread и дюрации"):
        for key in ("G-spread, бп", "Mod. duration, лет"):
            st.markdown(f"**{key}**")
            st.dataframe(cmp.attribution[key], hide_index=True, width="stretch",
                         column_config={"Вклад": st.column_config.NumberColumn(format="%+.2f")})

    if cmp.allocation is not None:
        st.subheader("Аллокация по сегментам")
        al = cmp.allocation
        fig = go.Figure()
        for name, col, color in (("Ранее", "Ранее", C1), ("Сейчас", "Текущее", C2), ("Цель", "Цель", C3)):
            fig.add_bar(y=al["Sleeve"], x=al[col] * 100, name=name, orientation="h", marker_color=color,
                        marker_line_width=0, hovertemplate="%{y}<br>" + name + ": %{x:.1f}%<extra></extra>")
        fig.update_layout(height=380, margin=dict(l=10, r=10, t=30, b=10), bargap=0.25, bargroupgap=0.08,
                          legend=dict(orientation="h", yanchor="bottom", y=1.0, x=0),
                          paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                          xaxis=dict(ticksuffix="%", gridcolor="rgba(137,135,129,0.25)"),
                          yaxis=dict(autorange="reversed"))
        st.plotly_chart(fig, width="stretch", theme="streamlit")
        v = al.copy()
        for c in ("Цель", "Ранее", "Текущее", "Δ за период", "Отклонение"):
            v[c] = v[c] * 100
        st.dataframe(v, hide_index=True, width="stretch", column_config={
            "Цель": st.column_config.NumberColumn("Цель, %", format="%.1f"),
            "Ранее": st.column_config.NumberColumn(f"{_d(cmp.prev_date)}, %", format="%.1f"),
            "Текущее": st.column_config.NumberColumn(f"{_d(cmp.cur_date)}, %", format="%.1f"),
            "Δ за период": st.column_config.NumberColumn("Δ за период, п.п.", format="%+.1f"),
            "Отклонение": st.column_config.NumberColumn("Отклонение от цели, п.п.", format="%+.1f"),
            "Статус": "Статус сейчас"})

    st.subheader("Изменения по бумагам")
    only = st.checkbox("Показывать только бумаги с изменениями", value=True)
    pv = pos[pos["Изменения"] != ""] if only else pos
    pv = pv.copy()
    for c in ("Вес ранее", "Вес сейчас", "YTM ранее", "YTM сейчас"):
        pv[c] = pv[c] * 100
    st.dataframe(pv.style.apply(lambda r: [f"background-color: {ROW_BG[r['Статус']]}; color: #1a1a1a"
                                           if r["Статус"] in ROW_BG else ""] * len(r), axis=1),
                 hide_index=True, width="stretch", column_config={
        **{c: st.column_config.NumberColumn(format="%.2f") for c in ("Вес ранее", "Вес сейчас", "MD ранее", "MD сейчас")},
        **{c: st.column_config.NumberColumn(format="%.3f") for c in ("YTM ранее", "YTM сейчас")},
        **{c: st.column_config.NumberColumn(format="%.0f") for c in ("G-spread ранее", "G-spread сейчас")},
        **{c: st.column_config.NumberColumn(format="%+.1f") for c in ("Δ вес, п.п.",)},
        **{c: st.column_config.NumberColumn(format="%+.0f") for c in ("Δ YTM, бп", "Δ G-spread, бп", "Δ рейтинга, ступеней")},
        "Δ MD": st.column_config.NumberColumn(format="%+.2f"),
        "Изменения": st.column_config.TextColumn(width="large")})

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Изменения рейтингов**")
        if cmp.rating_changes.empty:
            st.caption("За период рейтинги бумаг не менялись.")
        else:
            st.dataframe(cmp.rating_changes, hide_index=True, width="stretch")
    with c2:
        st.markdown("**Изменения статуса лимитов концентрации**")
        if cmp.concentration_changes.empty:
            st.caption("За период статусы лимитов концентрации не изменились.")
        else:
            cc = cmp.concentration_changes.copy()
            cc["Ранее"], cc["Сейчас"] = cc["Ранее"] * 100, cc["Сейчас"] * 100
            st.dataframe(cc, hide_index=True, width="stretch", column_config={
                "Ранее": st.column_config.NumberColumn("Ранее, %", format="%.1f"),
                "Сейчас": st.column_config.NumberColumn("Сейчас, %", format="%.1f")})
