"""Вкладка «G-spread»: независимый пересчёт и сверка с файлом."""
from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st

from core.gspread import METHOD_LABEL, GSpreadResult

C1, C2 = "#2a78d6", "#eb6834"


def _c(x, n):
    return f"{x:.{n}f}".replace(".", ",")


def _d(d):
    return d.strftime("%d.%m.%Y") if d else "—"


def render(res: GSpreadResult, cfg):
    g = cfg["gspread"]
    st.caption(f"G-spread = YTM − UST, интерполяция по par-кривой Treasury на {_d(res.curve.date)}. "
               f"Основной способ — {METHOD_LABEL[res.primary]}, для сравнения — "
               f"{METHOD_LABEL['duration' if res.primary == 'maturity' else 'maturity']}.")

    k = st.columns(3)
    k[0].metric("Средний G-spread: файл", f"{res.portfolio['file']:.0f} бп")
    k[1].metric("Средний G-spread: пересчёт", f"{res.portfolio['recomputed']:.0f} бп",
                f"{res.portfolio['recomputed'] - res.portfolio['file']:+.0f} бп к файлу", delta_color="off",
                help="ETF и денежные позиции — по значениям из файла.")
    k[2].metric("Медианное расхождение по бумагам", f"{res.systematic['median']:+.0f} бп")

    if res.systematic["flag"]:
        st.warning(f"**Спреды в файле в среднем на {abs(res.systematic['median']):.0f} бп "
                   f"{'шире' if res.systematic['median'] < 0 else 'уже'} нашего пересчёта** — у всех бумаг "
                   f"и в одну сторону (порог {res.systematic['threshold']} бп). Скорее всего, другая кривая или "
                   "дата расчёта, а не ошибки по отдельным бумагам.")
    imp = res.implied
    if imp.get("duration") or imp.get("maturity"):
        best = min((m for m in ("maturity", "duration") if m in imp), key=lambda m: imp[m]["mae"])
        b = imp[best]
        st.info(f"**Похоже, спреды посчитаны по кривой на {_d(b['date'])}** ({METHOD_LABEL[best]}): "
                f"расхождение в среднем ±{_c(b['mae'], 1)} бп, разброс {_c(b['sd'], 1)} бп. 10Y UST тогда — "
                f"{_c(b['ust10'] * 100, 2)}%, на дату снимка — {_c(res.curve.at(10) * 100, 2)}%. Перебраны кривые "
                f"Treasury за {_d(imp['window'][0])}–{_d(imp['window'][1])}. Это гипотеза — дату и кривую "
                "стоит уточнить у команды.")

    st.subheader("Какой способ ближе к файлу")
    s = res.summary.copy()
    st.dataframe(s, hide_index=True, width="stretch", column_config={
        c: st.column_config.NumberColumn(format="%+.1f" if "асхожд" in c or "Медиана" in c else "%.1f")
        for c in s.columns if s[c].dtype.kind == "f"})
    st.caption("Средний модуль — среднее отклонение пересчёта от файла; разброс — насколько оно различается "
               "между бумагами. Малый разброс при большом среднем означает системный сдвиг.")

    t = res.table[res.table["Тип"] == "bond"].copy()
    st.subheader("Расхождение по бумагам (пересчёт − файл), бп")
    fig = go.Figure()
    for m, color in (("maturity", C1), ("duration", C2)):
        col = f"Δ {METHOD_LABEL[m]}"
        fig.add_scatter(y=t["Позиция"], x=t[col], mode="markers", name=METHOD_LABEL[m],
                        marker=dict(size=9, color=color, line=dict(width=2, color="rgba(0,0,0,0)")),
                        hovertemplate="%{y}: %{x:+.0f} бп<extra>" + METHOD_LABEL[m] + "</extra>")
    if res.systematic["median"] is not None:
        fig.add_vline(x=res.systematic["median"], line=dict(color="#898781", dash="dot", width=1))
    fig.add_vline(x=0, line=dict(color="#c3c2b7", width=1))
    fig.update_layout(height=max(320, 22 * len(t) + 80), margin=dict(l=10, r=10, t=30, b=10),
                      legend=dict(orientation="h", yanchor="bottom", y=1.0, x=0),
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      xaxis=dict(gridcolor="rgba(137,135,129,0.25)", ticksuffix=" бп", zeroline=False),
                      yaxis=dict(autorange="reversed"))
    st.plotly_chart(fig, width="stretch", theme="streamlit")
    st.caption(f"Пунктир — медианное расхождение основного способа. «Выброс» — отклонение от медианы "
               f"больше {g['outlier_bp']} бп.")

    view = res.table.copy()
    view["YTM"] = view["YTM"] * 100
    for c in ("UST на срок", "UST на дюрацию"):
        view[c] = view[c] * 100
    cols = ["Позиция", "Рейтинг", "YTM", "Срок, лет", "Mod. duration", "UST на срок", "UST на дюрацию",
            "G-spread в файле", "G (срок)", "G (дюрация)", f"Δ {METHOD_LABEL['maturity']}",
            f"Δ {METHOD_LABEL['duration']}", "Отклонение от общего, бп", "Статус", "Комментарий"]

    def hl(r):
        return ["background-color: #fff3cd; color: #1a1a1a" if r["Статус"] == "выброс" else ""] * len(r)

    st.dataframe(view[cols].style.apply(hl, axis=1), hide_index=True, width="stretch", column_config={
        "YTM": st.column_config.NumberColumn("YTM, %", format="%.3f"),
        "Срок, лет": st.column_config.NumberColumn(format="%.2f"),
        "Mod. duration": st.column_config.NumberColumn(format="%.2f"),
        "UST на срок": st.column_config.NumberColumn("UST на срок, %", format="%.3f"),
        "UST на дюрацию": st.column_config.NumberColumn("UST на дюрацию, %", format="%.3f"),
        **{c: st.column_config.NumberColumn(format="%.0f") for c in ("G-spread в файле", "G (срок)", "G (дюрация)")},
        **{c: st.column_config.NumberColumn(format="%+.0f") for c in cols if c.startswith("Δ") or c.startswith("Откл")},
        "Комментарий": st.column_config.TextColumn(width="large")})
