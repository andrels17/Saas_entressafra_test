from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "src/ui/pages/notificacoes.py"


def _load_notificacoes_module():
    backups = {name: sys.modules.get(name) for name in [
        "streamlit",
        "src.auth.roles",
        "src.auth.scope",
        "src.ui.core.styles",
        "src.utils.supabase_helpers",
        "src.utils.nav",
    ]}

    def _decorator(*args, **kwargs):
        def deco(fn):
            return fn
        if args and callable(args[0]) and len(args) == 1 and not kwargs:
            return args[0]
        return deco

    streamlit_mod = types.ModuleType("streamlit")
    streamlit_mod.cache_data = _decorator
    streamlit_mod.cache_resource = _decorator
    streamlit_mod.fragment = _decorator
    streamlit_mod.session_state = {}
    sys.modules["streamlit"] = streamlit_mod

    roles_mod = types.ModuleType("src.auth.roles")
    roles_mod.Role = type("Role", (), {"ADMIN_ROLES": tuple()})
    sys.modules["src.auth.roles"] = roles_mod

    scope_mod = types.ModuleType("src.auth.scope")
    scope_mod.get_my_scope = lambda *a, **k: {}
    sys.modules["src.auth.scope"] = scope_mod

    styles_mod = types.ModuleType("src.ui.core.styles")
    styles_mod.page_header = lambda *a, **k: None
    sys.modules["src.ui.core.styles"] = styles_mod

    sb_mod = types.ModuleType("src.utils.supabase_helpers")
    sb_mod.current_role = lambda: None
    sb_mod.current_tenant_id = lambda: None
    sb_mod.sb_for_user = lambda: None
    sys.modules["src.utils.supabase_helpers"] = sb_mod

    nav_mod = types.ModuleType("src.utils.nav")
    nav_mod.get_current_revisao = lambda: None
    sys.modules["src.utils.nav"] = nav_mod

    try:
        spec = importlib.util.spec_from_file_location("notificacoes_regression", MODULE_PATH)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        return module
    finally:
        for name, original in backups.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


mod = _load_notificacoes_module()


def test_build_alertas_classifies_no_progress_stale_and_deadline_risk(monkeypatch):
    monkeypatch.setattr(mod, "_semana_atual", lambda _inicio, _total: 3)

    tarefas = [
        {
            "equipamento_id": "eq-1",
            "status": "pendente",
            "updated_at": "2026-01-18T00:00:00Z",
            "observacao": "",
            "etapa_d": None,
            "etapa_r": None,
            "etapa_m": None,
            "servicos": {"nome": "Lubrificação", "setores": {"nome": "Motor"}},
            "equipamentos": {
                "id": "eq-1",
                "frota": "F-01",
                "modelo": "Trator",
                "equip_grupos": {"nome": "Grupo A", "departamento_id": "dep-1"},
            },
        },
        {
            "equipamento_id": "eq-1",
            "status": "travado",
            "updated_at": "2026-01-10T00:00:00Z",
            "observacao": "aguardando peça",
            "etapa_d": True,
            "etapa_r": None,
            "etapa_m": None,
            "servicos": {"nome": "Inspeção", "setores": {"nome": "Freios"}},
            "equipamentos": {
                "id": "eq-1",
                "frota": "F-01",
                "modelo": "Trator",
                "equip_grupos": {"nome": "Grupo A", "departamento_id": "dep-1"},
            },
        },
    ]
    revisao = {"data_inicio": "2026-01-01", "semanas_total": 4}

    alertas = mod._build_alertas(tarefas, revisao, dias_travado=5, dias_sem_update=5)

    assert alertas["semana_atual"] == 3
    assert alertas["semanas_total"] == 4

    assert len(alertas["travados"]) == 1
    assert alertas["travados"].iloc[0]["Grupo"] == "Grupo A"

    assert len(alertas["sem_inicio"]) == 1
    assert alertas["sem_inicio"].iloc[0]["Serviço"] == "Lubrificação"

    assert len(alertas["sem_update"]) == 1
    assert alertas["sem_update"].iloc[0]["Status"] == "pendente"

    assert len(alertas["risco_prazo"]) == 1
    risco = alertas["risco_prazo"].iloc[0]
    assert risco["% Atual"] == 17
    assert risco["% Esperado"] == 75
    assert risco["Atraso (p.p.)"] == 58


def test_resumo_por_grupo_consolidates_all_categories():
    alertas = {
        "travados": pd.DataFrame([{"Grupo": "Grupo A"}, {"Grupo": "Grupo A"}, {"Grupo": "Grupo B"}]),
        "sem_inicio": pd.DataFrame([{"Grupo": "Grupo B"}]),
        "sem_update": pd.DataFrame([{"Grupo": "Grupo A"}]),
        "risco_prazo": pd.DataFrame([{"Grupo": "Grupo B"}, {"Grupo": "Grupo B"}]),
    }

    resumo = mod._resumo_por_grupo(alertas)

    assert list(resumo["Grupo"]) == ["Grupo B", "Grupo A"]

    grupo_b = resumo[resumo["Grupo"] == "Grupo B"].iloc[0]
    assert grupo_b["Travados"] == 1
    assert grupo_b["Sem início"] == 1
    assert grupo_b["Parados"] == 0
    assert grupo_b["Risco prazo"] == 2
    assert grupo_b["Total alertas"] == 4
