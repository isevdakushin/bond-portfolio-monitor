"""Вкладка «Методология и инструкция» (текст — из core.methodology)."""
from __future__ import annotations

import streamlit as st

from core.methodology import markdown, sections
from core.template import build_template


def render(cfg):
    st.caption("Раздел собирается из кода и текущих настроек — таблицы колонок и порогов всегда актуальны.")
    c1, c2 = st.columns(2)
    c1.download_button("Скачать методологию (Markdown)", markdown(cfg).encode("utf-8"),
                       file_name="METHODOLOGY.md", mime="text/markdown")
    c2.download_button("Скачать шаблон файла (.xlsx)", build_template(), file_name="portfolio_template.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", key="tpl_tab")
    for i, (title, body) in enumerate(sections(cfg)):
        with st.expander(title, expanded=i < 2):
            st.markdown(body)
