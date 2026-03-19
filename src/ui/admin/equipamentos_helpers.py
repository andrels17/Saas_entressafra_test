import streamlit as st
import pandas as pd


# Tenta usar service role (mais robusto para telas admin, evita RLS)
get_supabase_service = None
for _path in (
    "src.db.supabase_client",
    "src.supabase_client",
        "supabase_client"):
    try:
        mod = __import__(_path, fromlist=["get_supabase_service"])
        get_supabase_service = getattr(mod, "get_supabase_service", None)
        if get_supabase_service:
            break
    except Exception:
        continue

get_supabase_service = None
for _path in (
    "src.db.supabase_client",
    "src.supabase_client",
        "supabase_client"):
    try:
        mod = __import__(_path, fromlist=["get_supabase_service"])
        get_supabase_service = getattr(mod, "get_supabase_service", None)
        if get_supabase_service:
            break
    except Exception:
        continue


OPTIONAL_COLS = ["modelo", "ano", "status"]

# Mapeamentos comuns (CSV "frotas" / ERP etc.)
AUTO_MAP = {
    "cod_equipamento": "frota",
    "codigo_equipamento": "frota",
    "equipamento": "frota",
    "frota": "frota",
    "descricao_equipamento": "modelo",
    "descrição_equipamento": "modelo",
    "descricao": "modelo",
    "modelo": "modelo",
    "ano_fabricacao": "ano",
    "ano_fabricação": "ano",
    "ano": "ano",
    "status": "status",
    "situacao": "status",
    "situação": "status",

    # Seu CSV de frotas
    # classe = departamento
    # classe_operacional = grupo
    "classe": "departamento",
    "classe_operacional": "grupo",
}


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip().lower() for c in df.columns]
    return df


def _coerce_types(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if "ano" in df.columns:
        df["ano"] = pd.to_numeric(df["ano"], errors="coerce").astype("Int64")
    return df


def _rerun():
    # Alguns projetos têm um helper nav.rerun_keep_menu(); se não existir, usa
    # st.rerun()
    try:
        import nav  # type: ignore
        if hasattr(nav, "rerun_keep_menu"):
            nav.rerun_keep_menu()
            return
    except Exception as _e:
        import logging; logging.getLogger("saas").warning("Erro em equipamentos_helpers: %s", _e)
    st.rerun()


def _read_csv_smart(file) -> pd.DataFrame:
    last_err = None
    for enc in ("utf-8", "latin1", "cp1252"):
        try:
            file.seek(0)
            return pd.read_csv(file, sep=None, engine="python", encoding=enc)
        except Exception as e:
            last_err = e
            try:
                file.seek(0)
                return pd.read_csv(file, sep=";", encoding=enc)
            except Exception as e2:
                last_err = e2
                continue
    raise Exception(f"Não foi possível ler o CSV. Detalhe: {last_err}")


def _detect_mapping(df_cols: list[str]) -> dict[str, str]:
    mapping = {}
    for c in df_cols:
        if c in AUTO_MAP:
            mapping[c] = AUTO_MAP[c]
    return mapping


def _chunked(iterable, n: int):
    for i in range(0, len(iterable), n):
        yield iterable[i:i + n]


def _safe_int(v):
    try:
        if pd.isna(v):
            return None
        return int(v)
    except Exception:
        return None


def _audit(
        sb,
        tenant_id: str,
        action: str,
        payload: dict,
        equipamento_id: str | None = None,
        user_id: str | None = None):
    """
    Auditoria best-effort.
    - Usa tabela 'equip_audit' (você criou).
    - Se não existir por algum motivo, ignora sem quebrar.
    """
    base = {
        "tenant_id": tenant_id,
        "action": action,
        "payload": payload,
    }
    if equipamento_id:
        base["equipamento_id"] = equipamento_id
    if user_id:
        base["user_id"] = user_id
    try:
        sb.table("equip_audit").insert(base).execute()
    except Exception as _e:
        import logging; logging.getLogger("saas").warning("equipamentos_helpers.py: %s", _e)


def _load_grupos(sb, tenant_id: str):
    try:
        grupos = (
            sb.table("equip_grupos")
            .select("id, nome, ativo")
            .eq("tenant_id", tenant_id)
            .eq("ativo", True)
            .order("nome")
            .execute()
            .data
        ) or []
    except Exception:
        grupos = (
            sb.table("equip_grupos")
            .select("id, nome")
            .eq("tenant_id", tenant_id)
            .order("nome")
            .execute()
            .data
        ) or []
    grupo_opts = {"Sem grupo": None}
    for g in grupos:
        grupo_opts[g["nome"]] = g["id"]
    gid_to_name = {v: k for k, v in grupo_opts.items() if v}
    return grupo_opts, gid_to_name


def _load_departamentos(sb, tenant_id: str):
    """Retorna mapas para departamentos (nome->id e id->nome)."""
    try:
        deps = (
            sb.table("departamentos")
            .select("id, nome")
            .eq("tenant_id", tenant_id)
            .order("nome")
            .execute()
            .data
        ) or []
    except Exception:
        deps = []

    name_to_id = {str(d["nome"]).strip().lower(): d["id"]
                  for d in deps if d.get("nome")}
    id_to_name = {d["id"]: str(d["nome"]).strip()
                  for d in deps if d.get("id") and d.get("nome")}
    return name_to_id, id_to_name


def _ensure_departamentos(
        sb, tenant_id: str, dep_names: list[str]) -> dict[str, str]:
    """Garante que departamentos existam (best-effort) e retorna map nome_lower->id atualizado."""
    dep_names = [str(x).strip() for x in dep_names if x and str(x).strip()]
    if not dep_names:
        return _load_departamentos(sb, tenant_id)[0]

    name_to_id, _ = _load_departamentos(sb, tenant_id)
    missing = []
    for n in dep_names:
        k = n.lower()
        if k not in name_to_id:
            missing.append(n)

    if missing:
        rows = [{"tenant_id": tenant_id, "nome": n, "ativo": True}
                for n in sorted(set(missing))]
        try:
            sb.table("departamentos").insert(rows).execute()
        except Exception:
            # Pode falhar por duplicidade (índice em lower(nome)) ou RLS;
            # seguimos e recarregamos.
            pass
        name_to_id, _ = _load_departamentos(sb, tenant_id)

    return name_to_id


def _ensure_grupos(sb,
                   tenant_id: str,
                   grupo_pairs: list[tuple[str,
                                           str | None]]) -> dict[tuple[str | None,
                                                                       str],
                                                                 str]:
    """Garante grupos (equip_grupos) e retorna map (departamento_id, nome_lower)->id.

    Isso suporta o mesmo nome de grupo em departamentos diferentes.
    """
    # Normaliza pares
    norm_pairs: list[tuple[str, str | None]] = []
    seen = set()
    for gname, dep_id in (grupo_pairs or []):
        g = str(gname or "").strip()
        if not g:
            continue
        key = (dep_id, g.lower())
        if key in seen:
            continue
        seen.add(key)
        norm_pairs.append((g, dep_id))

    # Carrega existentes
    try:
        grupos = (
            sb.table("equip_grupos")
            .select("id, nome, departamento_id")
            .eq("tenant_id", tenant_id)
            .execute()
            .data
        ) or []
    except Exception:
        grupos = []

    existing_map: dict[tuple[str | None, str], str] = {}
    null_dep_by_name: dict[str, str] = {}
    for g in grupos:
        nm = (g.get("nome") or "").strip()
        if not nm:
            continue
        dep = g.get("departamento_id")
        existing_map[(dep, nm.lower())] = g.get("id")
        if dep is None and nm.lower() not in null_dep_by_name:
            null_dep_by_name[nm.lower()] = g.get("id")

    inserts = []
    updates = []
    for gname, dep_id in norm_pairs:
        k = (dep_id, gname.lower())
        if k in existing_map:
            continue
        # Se existe um grupo com mesmo nome e departamento NULL, e agora veio dep_id,
        # atualizamos esse registro em vez de criar outro.
        if dep_id and gname.lower() in null_dep_by_name:
            updates.append((null_dep_by_name[gname.lower()], dep_id))
            continue
        inserts.append({"tenant_id": tenant_id, "nome": gname,
                       "departamento_id": dep_id})

    if inserts:
        try:
            sb.table("equip_grupos").insert(inserts).execute()
        except Exception:
            # Pode falhar por duplicidade / RLS; recarregamos.
            pass

    if updates:
        for gid, dep_id in updates:
            try:
                sb.table("equip_grupos").update({"departamento_id": dep_id}).eq(
                    "tenant_id", tenant_id).eq("id", gid).execute()
            except Exception as _e:
                import logging; logging.getLogger("saas").warning("equipamentos_helpers.py: %s", _e)

    # Recarrega e retorna mapa atualizado
    try:
        grupos = (
            sb.table("equip_grupos")
            .select("id, nome, departamento_id")
            .eq("tenant_id", tenant_id)
            .execute()
            .data
        ) or []
    except Exception:
        grupos = []

    out: dict[tuple[str | None, str], str] = {}
    for g in grupos:
        nm = (g.get("nome") or "").strip()
        if not nm:
            continue
        out[(g.get("departamento_id"), nm.lower())] = g.get("id")
    return out


def _load_user_names(sb, user_ids: list[str]) -> dict[str, str]:
    """
    Resolve user_id -> nome (best-effort):
      1) tenta user_profiles (colunas user_id, nome)
      2) se falhar, retorna mapa vazio
    """
    user_ids = [u for u in user_ids if u]
    if not user_ids:
        return {}
    out = {}
    try:
        for chunk in _chunked(user_ids, 200):
            rows = (
                sb.table("user_profiles")
                .select("user_id, nome")
                .in_("user_id", chunk)
                .execute()
                .data
            ) or []
            for r in rows:
                uid = r.get("user_id")
                nm = (r.get("nome") or "").strip()
                if uid and nm:
                    out[uid] = nm
    except Exception:
        return {}
    return out
