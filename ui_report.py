"""Вкладка «Отчёт»: предпросмотр и скачивание HTML (печать в PDF — кнопкой в самом отчёте)."""
from __future__ import annotations

import streamlit as st
import streamlit.components.v1 as components

from core.report import Report


def render(rep: Report, file_stem: str):
    c1, c2 = st.columns([2, 3])
    c1.download_button("Скачать отчёт (HTML)", rep.html.encode("utf-8"), file_name=f"report_{file_stem}.html",
                       mime="text/html", type="primary")
    c2.markdown(f"**Версия текста: {rep.version}.** Записка собрана по шаблонам из рассчитанных значений, "
                "без LLM; каждое число в ней берётся из расчёта, и это проверяет автотест.")
    st.caption("Чтобы получить PDF, скачанный файл нужно открыть в браузере и нажать «Печать / PDF» (формат A4). "
               "Ниже — предпросмотр записки.")
    components.html(rep.html, height=1400, scrolling=True)
