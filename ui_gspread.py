"""Вкладка «G-spread»: независимый пересчёт и сверка с файлом."""
from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st

from core.gspread import METHOD_LABEL, GSpreadResult

C1, C2 = "#2a78d6", "#eb6834"


def _d(d):
    return d.strftime("%d.%m.%Y") if d else "—"


def render(res: GSpreadResult, cfg):
    g = cfg["gspread"]
    st.caption(f"G-spread = YTM бумаги − доходность UST, линейно интерполированная по par-кривой Treasury на "
               f"{_d(res.curve.date)}. Основной способ — {METHOD_LABEL[res.primary]}; для сравнения — "
               f"{METHOD_LABEL['duration' if res.primary == 'maturity' else 'maturity']}.")

    k = st.columns(3)
    k[0].metric("Средний G-spread: файл", f"{res.portfolio['file']:.0f} бп")
    k[1].metric("Средний G-spread: пересчёт", f"{res.portfolio['recomputed']:.0f} бп",
                f"{res.portfolio['recomputed'] - res.portfolio['file']:+.0f} бп к файлу", delta_color="off",
                help="Для фонда и денежной позиции взяты значения из файла.")
    k[2].metric("Медианное расхождение по бумагам", f"{res.systematic['median']:+.0f} бп")

    if res.systematic["flag"]:
        st.warning(f"**Системное расхождение.** Пересчитанный G-spread {METHOD_LABEL[res.primary]} в среднем на "
                   f"{abs(res.systematic['median']):.0f} бп {'ниже' if res.systematic['median'] < 0 else 'выше'}, "
                   f"чем в файле, у всех бумаг в одну сторону (порог {res.systematic['threshold']} бп). Это признак "
                   "того, что спреды в файле посчитаны по другой кривой или на другую дату, а не проблемы "
                   "отдельных бумаг.")
    imp = res.implied
    if imp.get("duration") or imp.get("maturity"):
        best = min((m for m in ("maturity", "duration") if m in imp), key=lambda m: imp[m]["mae"])
        b = imp[best]
        st.info(f"**Диагностика (гипотеза, не факт).** Среди кривых Treasury за {_d(imp['window'][0])}–"
                f"{_d(imp['window'][1])} G-spread из файла лучше всего воспроизводится кривой на **{_d(b['date'])}** "
                f"при интерполяции **{METHOD_LABEL[best]}**: средний модуль расхождения {b['mae']:.1f} бп, разброс "
                f"{b['sd']:.1f} бп. 10Y UST тогда был {b['ust10'] * 100:.2f}%, на дату снимка — "
                f"{res.curve.at(10) * 100:.2f}%. Вероятно, спреды в файле рассчитаны раньше даты сборки — стоит "
                "уточнить у команды, на какую дату и по какой кривой они считаются.")

    st.subheader("Какой способ ближе к файлу")
    s = res.summary.copy()
    st.dataframe(s, hide_index=True, width="stretch", column_config={
        c: st.column_config.NumberColumn(format="%+.1f" if "асхожд" in c or "Медиана" in c else "%.1f")
        for c in s.columns if s[c].dtype.kind == "f"})
    st.caption("Средний модуль — насколько в среднем пересчёт отличается от файла; разброс — насколько "
               "расхождение различается между бумагами (малый разброс при большом среднем = системный сдвиг).")

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
    st.caption(f"Пунктир — медианное расхождение основного способа. «Выброс» — бумага, расхождение которой "
               f"отличается от медианного больше чем на {g['outlier_bp']} бп.")

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
