"""Admin UI package — all render functions available from this namespace."""
from src.ui.admin.usuarios import render_admin_usuarios
from src.ui.admin.departamentos import render_admin_departamentos
from src.ui.admin.grupos import render_admin_grupos
from src.ui.admin.equipamentos import render_admin_equipamentos
from src.ui.admin.integridade import render_admin_integridade
from src.ui.admin.setores_servicos import render_admin_setores_servicos
from src.ui.admin.templates import render_admin_templates
from src.ui.admin.revisoes import render_admin_revisoes
from src.ui.admin.branding_reports import render_admin_branding_reports

__all__ = [
    "render_admin_usuarios",
    "render_admin_departamentos",
    "render_admin_grupos",
    "render_admin_equipamentos",
    "render_admin_integridade",
    "render_admin_setores_servicos",
    "render_admin_templates",
    "render_admin_revisoes",
    "render_admin_branding_reports",
]
