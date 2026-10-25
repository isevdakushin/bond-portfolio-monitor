"""Блок «Поправки аналитика» во вкладке «Качество данных»."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from core import corrections as C


def state_key(name):
    return f"corr::{name}"


def get(name) -> list:
    return st.session_state.get(state_key(name), [])


def _set(name, items):
    st.session_state[state_key(name)] = items


def banner(log: pd.DataFrame):
    if log is None or log.empty:
        return
    n_ok = int((log["Статус"] == "применена").sum())
    n_bad = len(log) - n_ok
    msg = f"**Применено поправок аналитика: {n_ok}.** Исходный файл не изменён; все расчёты и отчёт — по исправленным данным."
    if n_bad:
        msg += f" Не применено: {n_bad} (значение в файле изменилось или позиции нет) — см. «Качество данных»."
    st.warning(msg)


def render(snap_raw, name: str, log: pd.DataFrame, source: bytes):
    st.subheader("Поправки аналитика")
    st.caption("Исправления поверх файла компании: исходный файл не меняется, каждая поправка — с основанием и в журнале "
               "отчёта. Поправка применяется, только если значение в файле всё ещё равно «было», поэтому файл поправок "
               "можно безопасно загрузить к следующему снимку.")
    items = get(name)
    done = {(c["label"], c["field"]) for c in items}

    sugg = [s for s in C.suggest(snap_raw) if (s["label"], s["field"]) not in done]
    if sugg:
        st.markdown("**Подсказки** — значения следуют из самого файла или из расчёта; отметьте нужные и при необходимости "
                    "поправьте значение и основание.")
        cur = snap_raw.positions.set_index("label")
        df = pd.DataFrame([{"Применить": False, "Позиция": s["label"], "Поле": C.FIELDS[s["field"]][0],
                            "Сейчас": C.fmt_value(s["field"], cur.loc[s["label"], s["field"]]),
                            "Предлагается": C.fmt_value(s["field"], s["new"]), "Основание": s["reason"]} for s in sugg])
        ed = st.data_editor(df, hide_index=True, width="stretch", key=f"sugg::{name}",
                            disabled=["Позиция", "Поле", "Сейчас"],
                            column_config={"Основание": st.column_config.TextColumn(width="large")})
        if st.button("Применить отмеченные", key=f"apply::{name}", type="primary"):
            new, errs = list(items), []
            for i, row in ed.iterrows():
                if row["Применить"]:
                    try:
                        new.append(C.make(snap_raw, row["Позиция"], sugg[i]["field"], row["Предлагается"], row["Основание"]))
                    except C.CorrectionError as e:
                        errs.append(f"{row['Позиция']} — {row['Поле']}: {e}")
            for e in errs:
                st.error(e)
            if len(new) > len(items):
                _set(name, new)
                st.rerun()

    with st.expander("Добавить поправку вручную"):
        with st.form(f"manual::{name}"):
            labels = list(snap_raw.positions["label"])
            c1, c2 = st.columns(2)
            lab = c1.selectbox("Позиция", labels)
            field = c2.selectbox("Поле", list(C.FIELDS), format_func=lambda f: C.FIELDS[f][0])
            val = st.text_input("Новое значение", help="Даты — ДД.ММ.ГГГГ; YTM, купон, вес — в процентах (4,5); "
                                                       "дюрация — в годах; G-spread — в бп; рейтинг — S&P/Fitch или Moody's.")
            reason = st.text_input("Основание (обязательно)")
            if st.form_submit_button("Добавить"):
                try:
                    _set(name, items + [C.make(snap_raw, lab, field, val, reason)])
                    st.rerun()
                except C.CorrectionError as e:
                    st.error(str(e))

    if items:
        st.markdown("**Журнал поправок**")
        view = log.drop(columns=["field", "key", "new_value"], errors="ignore").copy()
        view.insert(0, "Отменить", False)
        ed = st.data_editor(view, hide_index=True, width="stretch", key=f"log::{name}",
                            disabled=[c for c in view.columns if c != "Отменить"],
                            column_config={"Основание": st.column_config.TextColumn(width="large")})
        c1, c2, c3, c4 = st.columns(4)
        if c1.button("Отменить отмеченные", key=f"undo::{name}"):
            keep = [c for c, drop in zip(items, ed["Отменить"]) if not drop]
            _set(name, keep)
            st.rerun()
        c2.download_button("Файл поправок (JSON)", C.to_json(items).encode("utf-8"),
                           file_name=f"corrections_{Path(name).stem}.json", mime="application/json",
                           help="Сохраните, чтобы применить те же поправки к следующему снимку.")
        xlsx = C.corrected_excel(source, snap_raw, log) if Path(name).suffix.lower() in (".xlsx", ".xlsm") else None
        if xlsx:
            c3.download_button("Исправленный Excel", xlsx, file_name=f"{Path(name).stem}_исправлено.xlsx",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                               help="Копия файла: изменённые ячейки подсвечены и с примечанием, журнал — на отдельном листе.")
        c4.download_button("Журнал (CSV)", view.drop(columns="Отменить").to_csv(index=False).encode("utf-8-sig"),
                           file_name="corrections_log.csv", mime="text/csv")

    up = st.file_uploader("Загрузить файл поправок (JSON)", type=["json"], key=f"upload::{name}")
    if up is not None and st.session_state.get(f"loaded::{name}") != up.file_id:
        try:
            _set(name, C.from_json(up.getvalue().decode("utf-8")))
            st.session_state[f"loaded::{name}"] = up.file_id
            st.rerun()
        except (C.CorrectionError, ValueError) as e:
            st.error(f"Файл поправок не загружен: {e}")
