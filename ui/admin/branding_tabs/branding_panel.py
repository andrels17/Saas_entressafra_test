"""Painel de branding white-label do tenant."""
import streamlit as st
from typing import Any, Dict
from src.ui.admin.branding_tabs.pdf_utils import load_branding, save_branding, Branding


def render_tab_branding(sb, tenant_id: str) -> Branding:
    """Renderiza o painel de branding e retorna o objeto Branding atual (DB ou defaults)."""
    branding_db = load_branding(sb, tenant_id)

    with st.expander("🎨 Branding (white-label) – opcional", expanded=False):
        company_name  = st.text_input("Nome da empresa (no relatório)", value=branding_db.get("company_name") or "AgroSafra")
        logo_url      = st.text_input("Logo URL (PNG/JPG)", value=branding_db.get("logo_url") or "")
        primary_color = st.text_input("Cor primária (hex)", value=branding_db.get("primary_color") or "#FFD100")
        accent_color  = st.text_input("Cor de destaque (hex)", value=branding_db.get("accent_color") or "#7F1D1D")
        footer_note   = st.text_input("Rodapé do relatório", value=branding_db.get("footer_note") or "Relatório gerado automaticamente.")

        if st.button("Salvar branding", icon=":material/save:", use_container_width=True):
            save_branding(sb, tenant_id, {
                "company_name":  company_name.strip(),
                "logo_url":      logo_url.strip() or None,
                "primary_color": primary_color.strip(),
                "accent_color":  accent_color.strip(),
                "footer_note":   footer_note.strip(),
            })
            st.success("Branding salvo.")

        st.caption("Dica: para logo, use um link público (Storage do Supabase com bucket público funciona bem).")

    # Retorna Branding com valores do DB (ignora o que foi digitado se não salvo)
    return Branding.from_db(branding_db)
