"""Вкладка «Оценка на сегодня и стресс-тест»."""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core import narrative as N
from core.estimate import Estimate
from core.stress import run_scenario

POS, NEG = "#2a78d6", "#e34948"


def _d(d):
    return d.strftime("%d.%m.%Y") if d else "—"


def render_estimate(e: Estimate | None, errors: list):
    st.subheader("Оценка портфеля на последнюю рыночную дату")
    if e is None:
        st.warning("Оценку построить не удалось: нет кривой UST или индексов FRED на последнюю дату. "
                   + " ".join(errors))
        return
    p = e.portfolio
    st.markdown(N.estimate(e))
    st.caption(f"Период — с {_d(e.as_of)} по {_d(e.est_date)}. Изменение доходности каждой бумаги оценено как "
               "изменение доходности UST на её срок (Treasury) плюс изменение OAS её индекса ICE BofA (FRED); "
               "изменение цены — через модифицированную дюрацию, к нему добавлен купонный доход за период.")
    k = st.columns(4)
    k[0].metric("YTM (оценка)", f"{p['ytm_est'] * 100:.2f}%", f"{(p['ytm_est'] - p['ytm_file']) * 1e4:+.0f} бп к файлу",
                delta_color="off")
    k[1].metric("G-spread (оценка)", f"{p['gs_est']:.0f} бп", f"{p['gs_est'] - p['gs_file']:+.0f} бп к файлу",
                delta_color="off")
    k[2].metric("Ценовой эффект + купон", f"{p['total'] * 100:+.2f}%",
                f"цена {p['price'] * 100:+.2f}%, купон {p['carry'] * 100:+.2f}%", delta_color="off")
    k[3].metric("Вклад: UST / спреды", f"{round(p['d_ust_bp']) + 0:+d} / {round(p['d_oas_bp']) + 0:+d} бп",
                help="Средневзвешенное изменение доходности UST на сроки бумаг и OAS их индексов: показывает, "
                     "что больше повлияло на оценку — ставки или спреды.")
    st.markdown("**Индексы и изменения спредов**")
    idx = e.indices.copy()
    st.dataframe(idx, hide_index=True, width="stretch", column_config={
        "Ссылка": st.column_config.LinkColumn("Источник", display_text="FRED"),
        "OAS на дату портфеля, бп": st.column_config.NumberColumn(format="%.0f"),
        "OAS на дату оценки, бп": st.column_config.NumberColumn(format="%.0f"),
        "Δ OAS, бп": st.column_config.NumberColumn(format="%+.0f")})
    pos = e.positions.sort_values("Итого доходность").copy()
    for c in ("Вес", "YTM в файле", "YTM (оценка)", "Ценовой эффект", "Купонный доход", "Итого доходность"):
        pos[c] = pos[c] * 100
    with st.expander("Оценка по позициям", expanded=False):
        st.dataframe(pos, hide_index=True, width="stretch", column_config={
            "Вес": st.column_config.NumberColumn("Вес, %", format="%.1f"),
            "YTM в файле": st.column_config.NumberColumn("YTM в файле, %", format="%.2f"),
            "YTM (оценка)": st.column_config.NumberColumn("YTM (оценка), %", format="%.2f"),
            **{c: st.column_config.NumberColumn(format="%+.1f") for c in ("Δ UST, бп", "Δ OAS индекса, бп", "Δ YTM (оценка), бп")},
            **{c: st.column_config.NumberColumn(format="%.0f") for c in ("G-spread в файле", "G-spread (оценка)")},
            **{c: st.column_config.NumberColumn(c + ", %", format="%+.2f") for c in ("Ценовой эффект", "Купонный доход",
                                                                                   "Итого доходность")}})
    for n in e.notes:
        st.caption(n)


def render_stress(snap, stress: dict):
    st.subheader("Стресс-тест: мгновенный шок ставок и спредов")
    st.markdown(N.stress(stress))
    st.caption("P&L позиции ≈ −модифицированная дюрация × (изменение UST + изменение спреда). Шок EM применяется "
               "к позициям с регионом EM, шок HY — к бумагам с рейтингом ниже BBB−; если бумага относится к обоим, "
               "берётся больший шок. Денежные позиции и T-bills реагируют только на ставки. Выпуклость не "
               "учитывается, поэтому при больших шоках оценка потерь немного завышена.")
    nav = st.number_input("NAV портфеля, $ млн (для P&L в долларах)", 0.0, 100000.0, 0.0, 0.1,
                          help="В файле компании NAV не указан. Если оставить 0, P&L показывается только в "
                               "процентах NAV.")
    s = stress["summary"].copy()
    fig = go.Figure()
    fig.add_bar(y=s["Сценарий"], x=s["P&L портфеля, % NAV"] * 100, orientation="h",
                marker_color=[POS if v >= 0 else NEG for v in s["P&L портфеля, % NAV"]], marker_line_width=0,
                text=[f"{v * 100:+.2f}%" for v in s["P&L портфеля, % NAV"]], textposition="outside", cliponaxis=False,
                hovertemplate="%{y}: %{x:+.2f}% NAV<extra></extra>")
    fig.add_vline(x=0, line=dict(color="#c3c2b7", width=1))
    fig.update_layout(height=60 * len(s) + 60, margin=dict(l=10, r=50, t=10, b=10), showlegend=False,
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      xaxis=dict(ticksuffix="%", gridcolor="rgba(137,135,129,0.25)", zeroline=False),
                      yaxis=dict(autorange="reversed"))
    st.plotly_chart(fig, width="stretch", theme="streamlit")
    view = s.copy()
    view["P&L портфеля, % NAV"] *= 100
    view["P&L худшей позиции, % NAV"] *= 100
    if nav:
        view["P&L, $ млн"] = s["P&L портфеля, % NAV"] * nav
    st.dataframe(view, hide_index=True, width="stretch", column_config={
        "P&L портфеля, % NAV": st.column_config.NumberColumn(format="%+.2f"),
        "P&L худшей позиции, % NAV": st.column_config.NumberColumn(format="%+.2f"),
        "P&L, $ млн": st.column_config.NumberColumn(format="%+.2f")})

    st.markdown("**Свой сценарий**")
    st.caption("Шоки задаются в базисных пунктах; результат пересчитывается сразу и показан по сегментам.")
    c = st.columns(4)
    sc = {"name": "свой", "ust": c[0].number_input("UST, бп", -500, 500, 50, 10),
          "em": c[1].number_input("Спреды EM, бп", -500, 1000, 50, 10),
          "hy": c[2].number_input("Спреды HY, бп", -500, 1000, 100, 10),
          "ig": c[3].number_input("Спреды IG (не EM), бп", -500, 500, 0, 5)}
    d = run_scenario(snap, sc)
    total = d["P&L, % NAV"].sum()
    st.metric("P&L портфеля в своём сценарии", f"{total * 100:+.2f}% NAV" + (f" ≈ {total * nav:+.2f} $ млн" if nav else ""))
    by = d.groupby("Sleeve")["P&L, % NAV"].sum().sort_values() * 100
    st.dataframe(pd.DataFrame({"Sleeve": by.index, "P&L, % NAV": by.values}), hide_index=True, width="stretch",
                 column_config={"P&L, % NAV": st.column_config.NumberColumn(format="%+.2f")})
