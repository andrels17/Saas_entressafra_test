
import streamlit as st


def two_col_form(left_fn, right_fn=None):
    c1, c2 = st.columns(2, gap="small")
    with c1:
        if callable(left_fn):
            left_fn()
    with c2:
        if callable(right_fn):
            right_fn()


def action_row(primary_label: str, primary_key: str, secondary_label: str | None = None, secondary_key: str | None = None):
    c1, c2 = st.columns([0.5, 0.5], gap="small")
    primary = secondary = False
    with c1:
        primary = st.button(primary_label, use_container_width=True, key=primary_key)
    with c2:
        if secondary_label and secondary_key:
            secondary = st.button(secondary_label, use_container_width=True, key=secondary_key)
    return primary, secondary
