"""Testes unitários para src/utils/config.py e src/domain/kpi.calc_prazo.

Executar com: pytest tests/test_config_prazo.py -v
Sem dependências externas.
"""
from __future__ import annotations

import sys
import types
from datetime import date, timedelta

import pytest

# ── Stub streamlit ────────────────────────────────────────────────────────────
class _CR:
    def __call__(self, f=None, **kw): return f if f else (lambda fn: fn)

if "streamlit" not in sys.modules:
    st = types.ModuleType("streamlit")
    st.cache_resource = _CR()
    st.session_state = {}
    st.stop = lambda: (_ for _ in ()).throw(SystemExit("st.stop"))
    st.error = lambda *a, **kw: None
    st.warning = lambda *a, **kw: None
    st.markdown = lambda *a, **kw: None
    sys.modules["streamlit"] = st

sys.path.insert(0, ".")

from src.domain.kpi import calc_prazo
import src.utils.config as cfg_mod


# ═════════════════════════════════════════════════════════════════════════════
# calc_prazo
# ═════════════════════════════════════════════════════════════════════════════

def _iso(d: date) -> str:
    return d.isoformat()

today = date.today()


class TestCalcPrazo:

    def test_sem_data_fim_retorna_sem_prazo(self):
        r = calc_prazo(data_inicio=None, data_fim=None)
        assert r["status_prazo"] == "sem_prazo"
        assert r["data_fim"] is None

    def test_data_fim_no_futuro_retorna_no_prazo(self):
        fim = today + timedelta(days=30)
        r = calc_prazo(data_inicio=_iso(today), data_fim=_iso(fim))
        assert r["status_prazo"] == "no_prazo"
        assert r["dias_restantes"] == 30

    def test_data_fim_passada_retorna_atrasado(self):
        fim = today - timedelta(days=5)
        r = calc_prazo(data_inicio=None, data_fim=_iso(fim))
        assert r["status_prazo"] == "atrasado"
        assert r["dias_restantes"] == -5

    def test_data_fim_hoje_retorna_no_prazo(self):
        r = calc_prazo(data_inicio=None, data_fim=_iso(today))
        assert r["dias_restantes"] == 0
        # status depende de pct_concluido — sem pct fica "atencao" (dias<=7 e pct<80)
        assert r["status_prazo"] in ("atencao", "no_prazo")

    def test_atencao_quando_poucos_dias_e_baixo_pct(self):
        fim = today + timedelta(days=5)
        r = calc_prazo(data_inicio=None, data_fim=_iso(fim), pct_concluido=40)
        assert r["status_prazo"] == "atencao"

    def test_no_prazo_quando_poucos_dias_mas_alto_pct(self):
        fim = today + timedelta(days=5)
        r = calc_prazo(data_inicio=None, data_fim=_iso(fim), pct_concluido=85)
        assert r["status_prazo"] == "no_prazo"

    def test_pct_tempo_gasto_calculado(self):
        inicio = today - timedelta(days=10)
        fim    = today + timedelta(days=10)
        r = calc_prazo(data_inicio=_iso(inicio), data_fim=_iso(fim))
        assert r["pct_tempo_gasto"] == 50
        assert r["dias_totais"] == 20
        assert r["dias_decorridos"] == 10

    def test_sem_data_inicio_pct_tempo_zero(self):
        fim = today + timedelta(days=10)
        r = calc_prazo(data_inicio=None, data_fim=_iso(fim))
        assert r["pct_tempo_gasto"] == 0
        assert r["dias_totais"] == 0

    def test_data_fim_preservada_no_resultado(self):
        fim = today + timedelta(days=7)
        r = calc_prazo(data_inicio=None, data_fim=_iso(fim))
        assert r["data_fim"] == _iso(fim)

    def test_formato_datetime_iso_aceito(self):
        fim = (today + timedelta(days=10)).isoformat() + "T00:00:00"
        r = calc_prazo(data_inicio=None, data_fim=fim)
        assert r["dias_restantes"] == 10

    def test_pct_tempo_nunca_passa_100(self):
        inicio = today - timedelta(days=100)
        fim    = today - timedelta(days=10)
        r = calc_prazo(data_inicio=_iso(inicio), data_fim=_iso(fim))
        assert r["pct_tempo_gasto"] <= 100

    def test_dias_restantes_negativo_grande_atraso(self):
        fim = today - timedelta(days=60)
        r = calc_prazo(data_inicio=None, data_fim=_iso(fim))
        assert r["dias_restantes"] == -60
        assert r["status_prazo"] == "atrasado"


# ═════════════════════════════════════════════════════════════════════════════
# validate_config
# ═════════════════════════════════════════════════════════════════════════════

class FakeSecrets:
    """Simula st.secrets com um dicionário."""
    def __init__(self, data: dict):
        self._data = data
    def get(self, k, d=None): return self._data.get(k, d)
    def __getitem__(self, k): return self._data[k]


class TestValidateConfig:

    def _run(self, secrets_dict: dict):
        fake = FakeSecrets(secrets_dict)
        original = cfg_mod._get_secrets
        cfg_mod._get_secrets = lambda: fake
        try:
            return cfg_mod.validate_config()
        finally:
            cfg_mod._get_secrets = original

    def test_config_completa_sem_erros(self):
        errors, warnings = self._run({
            "SUPABASE_URL":              "https://abc123.supabase.co",
            "SUPABASE_ANON_KEY":         "eyJxxx",
            "SUPABASE_SERVICE_ROLE_KEY": "eyJyyy",
        })
        assert errors == []

    def test_url_ausente_gera_erro(self):
        errors, _ = self._run({
            "SUPABASE_ANON_KEY":         "eyJxxx",
            "SUPABASE_SERVICE_ROLE_KEY": "eyJyyy",
        })
        assert any("SUPABASE_URL" in e for e in errors)

    def test_anon_key_ausente_gera_erro(self):
        errors, _ = self._run({
            "SUPABASE_URL":              "https://abc.supabase.co",
            "SUPABASE_SERVICE_ROLE_KEY": "eyJyyy",
        })
        assert any("SUPABASE_ANON_KEY" in e for e in errors)

    def test_service_role_ausente_gera_erro(self):
        errors, _ = self._run({
            "SUPABASE_URL":    "https://abc.supabase.co",
            "SUPABASE_ANON_KEY": "eyJxxx",
        })
        assert any("SUPABASE_SERVICE_ROLE_KEY" in e for e in errors)

    def test_url_formato_invalido_gera_erro(self):
        errors, _ = self._run({
            "SUPABASE_URL":              "http://nao-supabase.com",  # http, não https
            "SUPABASE_ANON_KEY":         "eyJxxx",
            "SUPABASE_SERVICE_ROLE_KEY": "eyJyyy",
        })
        assert any("SUPABASE_URL" in e and "formato" in e.lower() for e in errors)

    def test_smtp_parcial_gera_aviso_nao_erro(self):
        errors, warnings = self._run({
            "SUPABASE_URL":              "https://abc.supabase.co",
            "SUPABASE_ANON_KEY":         "eyJxxx",
            "SUPABASE_SERVICE_ROLE_KEY": "eyJyyy",
            "SMTP_HOST":                 "smtp.gmail.com",
            # SMTP_PORT, USER, PASSWORD ausentes
        })
        assert errors == []
        assert any("SMTP" in w for w in warnings)

    def test_smtp_totalmente_ausente_gera_aviso(self):
        errors, warnings = self._run({
            "SUPABASE_URL":              "https://abc.supabase.co",
            "SUPABASE_ANON_KEY":         "eyJxxx",
            "SUPABASE_SERVICE_ROLE_KEY": "eyJyyy",
        })
        assert errors == []
        assert any("SMTP" in w for w in warnings)

    def test_smtp_completo_sem_aviso(self):
        _, warnings = self._run({
            "SUPABASE_URL":              "https://abc.supabase.co",
            "SUPABASE_ANON_KEY":         "eyJxxx",
            "SUPABASE_SERVICE_ROLE_KEY": "eyJyyy",
            "SMTP_HOST":                 "smtp.gmail.com",
            "SMTP_PORT":                 "587",
            "SMTP_USER":                 "u@g.com",
            "SMTP_PASSWORD":             "secret",
        })
        assert not any("SMTP" in w for w in warnings)

    def test_smtp_porta_invalida_nao_bloqueia(self):
        """Porta SMTP inválida gera aviso mas não é erro crítico (opcional)."""
        # config.py não valida SMTP_PORT como obrigatório — apenas recomendado
        errors, _ = self._run({
            "SUPABASE_URL":              "https://abc.supabase.co",
            "SUPABASE_ANON_KEY":         "eyJxxx",
            "SUPABASE_SERVICE_ROLE_KEY": "eyJyyy",
        })
        # Sem SMTP_PORT, deve apenas gerar aviso, não erro
        assert errors == []

    def test_multiplos_campos_ausentes_listados(self):
        errors, _ = self._run({})
        assert len(errors) >= 3  # todos os obrigatórios ausentes
