import uuid

import streamlit as st
import streamlit.components.v1 as components


def force_full_reload(reason: str = "reload") -> None:
    """Força um reload completo do navegador com cache-buster.

    No Streamlit Cloud, F5 frequentemente reconecta na mesma sessão do servidor.
    Este redirect com query param aleatório força uma nova conexão/sessão.
    """
    bust = uuid.uuid4().hex

    try:
        qp = dict(st.query_params)
    except Exception:
        qp = {}

    qp["_bust"] = bust
    qp["_reason"] = reason

    qs = "&".join([f"{k}={v}" for k, v in qp.items()])
    js = f"""
    <script>
      const base = window.location.origin + window.location.pathname;
      window.location.replace(base + "?{qs}");
    </script>
    """

    components.html(js, height=0, width=0)
    st.stop()
