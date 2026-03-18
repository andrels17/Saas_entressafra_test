"""Pages UI package.

Mantém imports leves para evitar efeitos colaterais durante testes unitários.
"""

__all__ = [
    "render_home_overview",
    "render_dashboard",
    "render_gestor_painel",
    "render_auditoria",
    "render_apontamento",
    "render_matriz",
]


def __getattr__(name: str):
    if name == "render_home_overview":
        from src.ui.pages.home_overview import render_home_overview
        return render_home_overview
    if name == "render_dashboard":
        from src.ui.pages.dashboard import render_dashboard
        return render_dashboard
    if name == "render_gestor_painel":
        from src.ui.pages.gestor_painel import render_gestor_painel
        return render_gestor_painel
    if name == "render_auditoria":
        from src.ui.pages.auditoria import render_auditoria
        return render_auditoria
    if name == "render_apontamento":
        from src.ui.pages.apontamento import render_apontamento
        return render_apontamento
    if name == "render_matriz":
        from src.ui.pages.matriz import render_matriz
        return render_matriz
    raise AttributeError(name)
