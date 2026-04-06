"""Histórico de comentários por tarefa.

Componente reutilizável que exibe e permite adicionar comentários
com timestamp e usuário, substituindo o campo observação único.

Uso:
    from src.ui.components.comentarios import render_comentarios
    render_comentarios(tenant_id, tarefa_id, current_user_id)

Requer tabela `tarefa_comentarios` no Supabase:
    id uuid PK default gen_random_uuid()
    tenant_id uuid FK
    tarefa_id uuid FK tarefas_servico(id)
    user_id uuid
    user_nome text
    texto text not null
    created_at timestamptz default now()
"""
from __future__ import annotations
from html import escape as _h

import streamlit as st
from datetime import datetime

from src.ui.core.cache import bump_data_version, clear_cached_functions
from src.utils.supabase_helpers import sb_for_user, current_user_id


@st.cache_data(ttl=30, show_spinner=False)
def _load_comentarios(
        tenant_id: str,
        tarefa_id: str,
        ver: str = "0") -> list[dict]:
    try:
        sb = sb_for_user()
        rows = (
            sb.table("tarefa_comentarios")
            .select("id,texto,user_nome,created_at")
            .eq("tenant_id", tenant_id)
            .eq("tarefa_id", tarefa_id)
            .order("created_at", desc=False)
            .execute()
            .data
        ) or []
        return rows
    except Exception:
        return []


def _fmt_dt(dt_str: str | None) -> str:
    if not dt_str:
        return ""
    try:
        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        return dt.strftime("%d/%m/%Y %H:%M")
    except Exception:
        return dt_str[:16] if dt_str else ""


def render_comentarios(
    tenant_id: str,
    tarefa_id: str,
    *,
    user_nome: str = "Usuário",
    compact: bool = False,
    key_prefix: str = "",
) -> None:
    """Renderiza o histórico de comentários e campo para novo comentário.

    Args:
        tenant_id: ID do tenant atual.
        tarefa_id: ID da tarefa a ser comentada.
        user_nome: Nome do usuário logado para exibir no comentário.
        compact: Se True, usa layout mais compacto (para uso em expanders).
        key_prefix: Prefixo único para evitar conflito de widget keys.
    """
    ver = str(st.session_state.get("data_version", "0"))
    comentarios = _load_comentarios(tenant_id, tarefa_id, ver)

    label = f"💬 Histórico ({
        len(comentarios)})" if not compact else f"💬 {
        len(comentarios)}"

    with st.expander(label, expanded=(len(comentarios) > 0 and not compact)):
        if not comentarios:
            st.caption("Nenhum comentário ainda.")
        else:
            for c in comentarios:
                nome = c.get("user_nome") or "Usuário"
                txt = c.get("texto") or ""
                dt = _fmt_dt(c.get("created_at"))
                avatar = (nome[:1] or "U").upper()
                st.markdown(
                    f'<div style="display:flex;gap:8px;margin-bottom:10px;align-items:flex-start">'
                    f'<div style="width:28px;height:28px;border-radius:50%;background:#1F2937;'
                    f'display:flex;align-items:center;justify-content:center;'
                    f'font-size:.75rem;font-weight:700;color:#9CA3AF;flex-shrink:0">{avatar}</div>'
                    f'<div style="flex:1;background:rgba(255,255,255,.04);border-radius:8px;'
                    f'padding:7px 10px;border:1px solid rgba(255,255,255,.07)">'
                    f'<div style="display:flex;justify-content:space-between;margin-bottom:3px">'
                    f'<span style="font-size:.78rem;font-weight:600;color:#D1D5DB">{_h(str(nome))}</span>'
                    f'<span style="font-size:.72rem;color:rgba(255,255,255,.35)">{dt}</span>'
                    f'</div>'
                    f'<div style="font-size:.83rem;color:rgba(255,255,255,.8);line-height:1.4">{_h(str(txt))}</div>'
                    f'</div></div>',
                    unsafe_allow_html=True,
                )

        # Campo para novo comentário
        st.markdown(
            '<div style="margin-top:4px"></div>',
            unsafe_allow_html=True)
        _key_txt = f"{key_prefix}comt_txt_{tarefa_id}"
        _key_btn = f"{key_prefix}comt_btn_{tarefa_id}"
        novo_txt = st.text_area(
            "Novo comentário",
            key=_key_txt,
            height=70,
            placeholder="Descreva o impedimento, peça aguardada, ocorrência…",
            label_visibility="collapsed",
        )
        if st.button(
            "➕ Adicionar comentário",
            key=_key_btn,
            use_container_width=True,
                type="secondary"):
            if not (novo_txt or "").strip():
                st.warning("Digite algo antes de adicionar.")
            else:
                try:
                    sb = sb_for_user()
                    sb.table("tarefa_comentarios").insert({
                        "tenant_id": tenant_id,
                        "tarefa_id": tarefa_id,
                        "user_id": current_user_id() or None,
                        "user_nome": user_nome,
                        "texto": novo_txt.strip(),
                    }).execute()
                    st.toast("✅ Comentário adicionado!")
                    # Limpar campo e invalidar cache
                    st.session_state.pop(_key_txt, None)
                    bump_data_version()
                    clear_cached_functions(_load_comentarios)
                    st.rerun()
                except Exception as e:
                    st.error(
                        f"Erro ao salvar comentário. Verifique se a tabela "
                        f"`tarefa_comentarios` existe no Supabase. Detalhes: {e}"
                    )
