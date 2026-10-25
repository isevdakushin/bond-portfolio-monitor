"""Блок «Распознавание файла»: что инструмент нашёл в файле и ручная правка (лист, колонки, дата)."""
from __future__ import annotations

import datetime as dt

import streamlit as st

from core.loader import COLUMN_LABELS, ESSENTIAL, NONE_CHOICE

KEY_ORDER = ["issuer", "isin", "weight", "market_value", "ytm", "gspread", "mod_duration", "maturity", "coupon",
             "rating", "sleeve", "credit_bucket", "region", "country", "sector", "num", "min_piece"]
ESSENTIAL_KEYS = {k for alt in ESSENTIAL for k in alt}


def state_key(name):
    return f"overrides::{name}"


def get_overrides(name) -> dict:
    return st.session_state.get(state_key(name), {})


def render(name, detected: dict, as_of=None, expanded=False):
    """Показывает распознанное и форму ручной правки. Возвращает True, если пользователь применил правки."""
    labels = detected.get("labels", [])
    cols = detected.get("columns", {})
    current = get_overrides(name)
    with st.expander("Распознавание файла: лист, колонки, дата", expanded=expanded):
        st.caption(f"Лист с позициями: «{detected.get('sheet', '—')}», строка заголовков: "
                   f"{detected.get('header_row', '—')}. Ошибки распознавания исправляются здесь, затем «Применить».")
        with st.form(f"map::{name}"):
            sheets = detected.get("sheets", [])
            sheet = st.selectbox("Лист с позициями", sheets,
                                 index=sheets.index(detected["sheet"]) if detected.get("sheet") in sheets else 0) \
                if sheets else None
            options = [NONE_CHOICE] + labels
            chosen = {}
            grid = st.columns(3)
            for i, key in enumerate(KEY_ORDER):
                cur = current.get("columns", {}).get(key) or cols.get(key) or NONE_CHOICE
                label = COLUMN_LABELS[key] + (" *" if key in ESSENTIAL_KEYS else "")
                chosen[key] = grid[i % 3].selectbox(label, options,
                                                    index=options.index(cur) if cur in options else 0, key=f"{name}-{key}")
            st.caption("Звёздочка (\\*): нужны YTM и хотя бы по одному полю из пар «Issuer / ISIN» "
                       "и «Weight / Market value».")
            date_val = st.date_input("Дата снимка (если не найдена в файле или неверна)",
                                     value=current.get("as_of") or as_of, format="DD.MM.YYYY")
            applied = st.form_submit_button("Применить")
        if applied:
            col_over = {k: v for k, v in chosen.items() if v != (cols.get(k) or NONE_CHOICE)}
            new = {"columns": col_over}
            if sheet and sheet != detected.get("sheet"):
                new = {"sheet": sheet}            # другой лист — колонки распознаются заново
            if isinstance(date_val, dt.date) and date_val != as_of:
                new["as_of"] = date_val
            st.session_state[state_key(name)] = new
            return True
        if current and st.button("Сбросить ручные правки", key=f"reset::{name}"):
            st.session_state.pop(state_key(name), None)
            return True
    return False
