"""Pages UI package."""
from src.ui.pages.home_overview import render_home_overview
from src.ui.pages.dashboard import render_dashboard
from src.ui.pages.gestor_painel import render_gestor_painel
from src.ui.pages.auditoria import render_auditoria
from src.ui.pages.apontamento import render_apontamento
from src.ui.pages.matriz import render_matriz

__all__ = [
    "render_home_overview",
    "render_dashboard",
    "render_gestor_painel",
    "render_auditoria",
    "render_apontamento",
    "render_matriz",
]
