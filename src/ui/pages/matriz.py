from src.ui.pages.matriz_selection import render_selection
from src.ui.pages.matriz_sector import render_setores
from src.ui.pages.matriz_runtime import get_runtime_context


def render_matriz():
    ctx = get_runtime_context()

    # 1. seleção (grupo / depto)
    selection = render_selection(ctx)

    if not selection:
        return

    # 2. render setores
    render_setores(ctx, selection)
