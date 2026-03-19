
from __future__ import annotations

import streamlit as st


def form_section(title: str, description: str | None = None) -> None:
    st.markdown(f"### {title}")
    if description:
        st.caption(description)


def validate_required(fields: dict[str, object]) -> list[str]:
    errors: list[str] = []
    for label, value in fields.items():
        if value is None:
            errors.append(f"Informe {label.lower()}.")
            continue
        if isinstance(value, str) and not value.strip():
            errors.append(f"Informe {label.lower()}.")
    return errors


def validate_date_range(start, end, *, start_label: str = "a data de início", end_label: str = "a data final da revisão") -> list[str]:
    errors: list[str] = []
    if not start:
        errors.append(f"Informe {start_label}.")
    if not end:
        errors.append(f"Informe {end_label}.")
    if start and end and end < start:
        errors.append("A data fim não pode ser menor que a data início.")
    return errors


def validation_summary(errors: list[str], *, title: str = "Corrija antes de continuar") -> None:
    if not errors:
        return
    bullets = "".join(f"<li>{err}</li>" for err in errors)
    st.error(f"{title}\n\n" + "\n".join(f"• {err}" for err in errors))
    with st.expander("Ver detalhes da validação", expanded=False):
        st.markdown(f"<ul>{bullets}</ul>", unsafe_allow_html=True)


def form_submit_button(
    label: str,
    *,
    key: str,
    help: str | None = None,
    use_container_width: bool = True,
    disabled: bool = False,
) -> bool:
    kwargs = dict(
        key=key,
        type="primary",
        help=help,
        use_container_width=use_container_width,
    )
    try:
        return st.button(label, disabled=disabled, **kwargs)
    except TypeError:
        # Compatibilidade com versões mais antigas do Streamlit.
        if disabled:
            st.button(label, **kwargs)
            return False
        return st.button(label, **kwargs)


def validate_time_hhmm(value: str, *, label: str = "o horário") -> list[str]:
    errors: list[str] = []
    raw = (value or "").strip()
    try:
        hh, mm = raw.split(":")
        if not (0 <= int(hh) <= 23 and 0 <= int(mm) <= 59):
            raise ValueError
    except Exception:
        errors.append(f"Informe {label.lower()} no formato HH:MM (ex: 07:00).")
    return errors
