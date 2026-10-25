"""Вкладка «Методология и инструкция»: короткая инструкция сверху, подробная методология — в свёрнутых блоках."""
from __future__ import annotations

import streamlit as st

from core.methodology import details, guide
from core.template import build_template


def render(cfg):
    for title, body in guide(cfg):
        st.markdown(f"#### {title}")
        st.markdown(body)
    st.download_button("Скачать шаблон файла (.xlsx)", build_template(), file_name="portfolio_template.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", key="tpl_tab")
    st.divider()
    st.markdown("#### Подробная методология")
    st.caption("Формулы, проверки и допущения с обоснованием — для тех, кто хочет разобраться в деталях.")
    for title, body in details(cfg):
        with st.expander(title):
            st.markdown(body)
