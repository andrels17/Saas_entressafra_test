
from __future__ import annotations

from contextlib import contextmanager

import streamlit as st


def empty_message(
    title: str,
    body: str | None = None,
    *,
    kind: str = "info",
) -> None:
    if body:
        st.markdown(f"### {title}")
    message = body or title
    if kind == "success":
        st.success(message)
    elif kind == "warning":
        st.warning(message)
    elif kind == "error":
        st.error(message)
    else:
        st.info(message)


@contextmanager
def loading_block(label: str = "Carregando dados..."):
    with st.spinner(label):
        yield


def section_placeholder(title: str, body: str) -> None:
    st.markdown(f"### {title}")
    st.caption(body)
