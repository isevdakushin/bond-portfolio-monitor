"""Вкладка «Сигналы по спредам»."""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core import narrative as N

# Цвет — по сигналу (не больше трёх серий на точечной диаграмме): шире — оранжевый, уже — синий, норма — серый.
SIG_COLORS = {"шире группы": "#eb6834", "уже группы": "#2a78d6", "в норме": "#898781"}
ROW_BG = {"шире группы": "#fde2d6", "уже группы": "#dbe9fb", "аномальное расширение": "#fde2d6",
          "аномальное сужение": "#dbe9fb"}


def _style(df, col="Сигнал"):
    return df.style.apply(lambda r: [f"background-color: {ROW_BG[r[col]]}; color: #1a1a1a" if r[col] in ROW_BG
                                     else ""] * len(r), axis=1)


def render(rv: pd.DataFrame, changes: pd.DataFrame | None, cfg, prev_snap=None):
    s = cfg["signals"]
    src = "из файла" if s["spread_source"] == "file" else "пересчитанный по кривой Treasury"
    slope = f"{rv.attrs.get('slope', 0.0):.1f}".replace(".", ",")
    window = f"{s['duration_window']:g}".replace(".", ",")
    st.caption(f"Каждая облигация сравнивается с пирами — бумагами той же рейтинговой группы с модифицированной "
               f"дюрацией в пределах ±{window} г.; если таких бумаг меньше {s['min_peers']}, окно расширяется. "
               + (f"Спреды пиров приведены к дюрации бумаги: длинные бумаги обычно торгуются шире, и по портфелю "
                  f"этот наклон составляет {slope} б.п. на год дюрации. " if s.get("duration_adjust", True) else "")
               + f"Используется G-spread {src}. Сигнал «шире группы» появляется, если спред бумаги выше медианы "
                 f"пиров больше чем на {s['wide_abs_bp']:g} б.п. и больше чем на {s['wide_rel'] * 100:g}%.")

    wide = rv[rv["Сигнал"] == "шире группы"]
    narrow = rv[rv["Сигнал"] == "уже группы"]
    k = st.columns(3)
    k[0].metric("Шире группы", len(wide), help="Бумаги со спредом заметно выше пиров: рынок оценивает их риск выше, чем предполагает рейтинг.")
    k[1].metric("Уже группы", len(narrow), help="Бумаги со спредом заметно ниже пиров: компенсация за кредитный риск небольшая.")
    k[2].metric("Бумаг в анализе", len(rv))

    st.subheader("Шире пиров того же рейтинга")
    if wide.empty:
        st.success("Бумаг со спредом заметно шире пиров того же рейтинга нет.")
    for _, r in wide.iterrows():
        st.warning(N.wide(r))
    if not narrow.empty:
        st.subheader("Уже пиров того же рейтинга")
        for _, r in narrow.iterrows():
            st.info(N.narrow(r))
        st.caption("В группах из двух-трёх бумаг одна бумага с широким спредом сдвигает медиану, и остальные "
                   "выглядят «узкими», поэтому такие сигналы стоит оценивать вместе.")

    st.subheader("Спред и дюрация")
    fig = go.Figure()
    for sig, color in SIG_COLORS.items():
        d = rv[rv["Сигнал"] == sig]
        if d.empty:
            continue
        fig.add_scatter(x=d["Mod. duration"], y=d["G-spread"], mode="markers+text" if sig != "в норме" else "markers",
                        name=sig, text=d["Позиция"].str.replace(r"^#\d+ ", "", regex=True) if sig != "в норме" else None,
                        textposition="top center", marker=dict(size=10, color=color,
                                                               line=dict(width=2, color="rgba(255,255,255,0.6)")),
                        customdata=d[["Позиция", "Рейтинг", "Медиана пиров", "Премия, бп"]].values,
                        hovertemplate="%{customdata[0]} (%{customdata[1]})<br>MD %{x:.2f}, G-spread %{y:.0f} бп<br>"
                                      "медиана пиров %{customdata[2]:.0f} бп, премия %{customdata[3]:+.0f} бп"
                                      "<extra></extra>")
    fig.update_layout(height=420, margin=dict(l=10, r=10, t=30, b=10),
                      legend=dict(orientation="h", yanchor="bottom", y=1.0, x=0),
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      xaxis=dict(title="Mod. duration, лет", gridcolor="rgba(137,135,129,0.2)"),
                      yaxis=dict(title="G-spread, бп", gridcolor="rgba(137,135,129,0.2)"))
    st.plotly_chart(fig, width="stretch", theme="streamlit")

    with st.expander("Все бумаги: пиры и премия"):
        view = rv.copy()
        view["Премия, %"] = view["Премия, %"] * 100
        st.dataframe(_style(view), hide_index=True, width="stretch", column_config={
            "Mod. duration": st.column_config.NumberColumn(format="%.2f"),
            "G-spread": st.column_config.NumberColumn("G-spread, бп", format="%.0f"),
            "Медиана пиров": st.column_config.NumberColumn("Медиана пиров, бп", format="%.0f"),
            "Премия, бп": st.column_config.NumberColumn(format="%+.0f"),
            "Премия, %": st.column_config.NumberColumn(format="%+.0f"),
            "Пиры": st.column_config.TextColumn(width="large")})

    st.subheader("Аномальное расширение спреда между портфелями")
    if changes is None:
        st.info("Для этого раздела нужен предыдущий портфель — его можно загрузить в панели слева. Тогда здесь "
                "появятся бумаги, спред которых за период вырос заметно сильнее, чем у бумаг того же рейтинга.")
        return
    if changes.empty:
        st.info("В двух портфелях нет общих бумаг с G-spread в обоих файлах, поэтому изменение спредов сравнить "
                "не с чем.")
        return
    an = changes[changes["Сигнал"] == "аномальное расширение"]
    st.caption(f"Сравнение с портфелем на {prev_snap.as_of:%d.%m.%Y}, бумаг в сравнении: {len(changes)}. Сигнал "
               f"появляется, если спред бумаги вырос больше медианного изменения её рейтинговой группы более чем на "
               f"{s['widening_abs_bp']:g} б.п." if prev_snap
               and prev_snap.as_of else "")
    if an.empty:
        st.success("Ни у одной бумаги спред не вырос заметно сильнее, чем у её рейтинговой группы.")
    for _, r in an.iterrows():
        st.warning(N.widening(r))
    st.dataframe(_style(changes), hide_index=True, width="stretch", column_config={
        c: st.column_config.NumberColumn(format="%+.0f" if "Изменение" in c or "Сверх" in c or "Медианное" in c
                                         else "%.0f") for c in changes.columns if changes[c].dtype.kind == "f"})
