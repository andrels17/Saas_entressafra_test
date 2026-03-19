
import streamlit as st
import pandas as pd
from postgrest.exceptions import APIError

from src.ui.admin_components.layout import admin_block
from src.ui.admin.equipamentos_helpers import (
    OPTIONAL_COLS,
    _normalize_columns,
    _coerce_types,
    _rerun,
    _read_csv_smart,
    _detect_mapping,
    _chunked,
    _safe_int,
    _audit,
    _ensure_departamentos,
    _ensure_grupos,
)


def render_import_csv_tab(sb, tenant_id: str) -> None:
    admin_block(
        "Importar equipamentos",
        "Envie o CSV e mapeie colunas com segurança.")
    st.caption(
        "O sistema aceita CSV com separador `,` ou `;`, e tenta detectar encoding automaticamente.")
    st.caption(
        "Campos mínimos: `frota` (obrigatório). Opcional: `modelo`, `ano`, `status`.")
    st.download_button(
        "Baixar modelo CSV", icon=":material/download:",
        data=(
            "frota,modelo,ano,status,classe,classe_operacional\n"
            "2055,John Deere 6190J,2021,Parado,Tratores,4x4\n"
        ),
        file_name="modelo_equipamentos.csv",
        mime="text/csv",
        use_container_width=True,
    )

    admin_block(
        "Dicas para CSV do ERP",
        "Mapeamentos automáticos já suportados pelo importador.")
    with st.expander("Se seu CSV tem colunas como `cod_equipamento` e `descricao_equipamento`", expanded=False):
        st.caption("Nós mapeamos automaticamente:")
        st.code(
            "cod_equipamento → frota\n"
            "descricao_equipamento → modelo\n"
            "ano_fabricacao → ano\n"
            "situacao → status\n\n"
            "classe → departamento\n"
            "classe_operacional → grupo",
            language="text"
        )

    file = st.file_uploader("Enviar CSV", type=["csv"])
    if file is not None:
        try:
            df_raw = _read_csv_smart(file)
        except Exception as e:
            st.error(str(e))
            st.stop()

        df_raw = _normalize_columns(df_raw)

        detected = _detect_mapping(list(df_raw.columns))

        admin_block("Mapeamento de colunas",
                    "Confira e ajuste os campos antes de importar.")
        cols = list(df_raw.columns)

        def _pick_default(dest: str):
            for src, d in detected.items():
                if d == dest and src in cols:
                    return src
            if dest in cols:
                return dest
            return None

        def _sel(label, dest):
            default = _pick_default(dest)
            options = [None] + cols
            idx = options.index(default) if default in options else 0
            return st.selectbox(label, options=options, index=idx)

        map_frota = _sel(
            "Coluna que representa **FROTA** (obrigatório)",
            "frota")
        map_modelo = _sel(
            "Coluna que representa **MODELO** (opcional)",
            "modelo")
        map_ano = _sel("Coluna que representa **ANO** (opcional)", "ano")
        map_status = _sel(
            "Coluna que representa **STATUS** (opcional)",
            "status")
        map_departamento = _sel(
            "Coluna que representa **DEPARTAMENTO** (opcional)",
            "departamento")
        map_grupo = _sel("Coluna que representa **GRUPO** (opcional)", "grupo")

        if not map_frota:
            st.error("Selecione a coluna de FROTA para prosseguir.")
            st.stop()

        df = pd.DataFrame()
        df["frota"] = df_raw[map_frota]
        if map_modelo:
            df["modelo"] = df_raw[map_modelo]
        if map_ano:
            df["ano"] = df_raw[map_ano]
        if map_status:
            df["status"] = df_raw[map_status]
        if map_departamento:
            df["departamento"] = df_raw[map_departamento]
        if map_grupo:
            df["grupo"] = df_raw[map_grupo]

        df = _normalize_columns(df)
        df = _coerce_types(df)

        df["frota"] = df["frota"].astype(str).str.strip()
        df = df[df["frota"].ne("")].copy()

        if "modelo" in df.columns:
            df["modelo"] = df["modelo"].astype(str).str.strip()
            df.loc[df["modelo"].str.lower().isin(
                ["nan", "none"]), "modelo"] = ""
        if "status" in df.columns:
            df["status"] = df["status"].astype(str).str.strip()
            df.loc[df["status"].str.lower().isin(
                ["nan", "none"]), "status"] = ""

        if "departamento" in df.columns:
            df["departamento"] = df["departamento"].astype(str).str.strip()
            df.loc[df["departamento"].str.lower().isin(
                ["nan", "none"]), "departamento"] = ""
        if "grupo" in df.columns:
            df["grupo"] = df["grupo"].astype(str).str.strip()
            df.loc[df["grupo"].str.lower().isin(["nan", "none"]), "grupo"] = ""

        cols_keep = ["frota"] + [c for c in OPTIONAL_COLS if c in df.columns]
        for extra in ("departamento", "grupo"):
            if extra in df.columns:
                cols_keep.append(extra)
        df = df[cols_keep].drop_duplicates(subset=["frota"], keep="last")

        admin_block(
            "Prévia",
            "Confira os dados antes de confirmar a importação.")
        st.dataframe(df, use_container_width=True, hide_index=True)

        admin_block(
            "Diagnóstico",
            "Estimativa de novos registros e atualizações.")
        total = len(df)
        existing_frotas = set()
        try:
            frotas = df["frota"].tolist()
            for chunk in _chunked(frotas, 250):
                rows = (
                    sb.table("equipamentos")
                    .select("frota")
                    .eq("tenant_id", tenant_id)
                    .in_("frota", chunk)
                    .execute()
                    .data
                ) or []
                existing_frotas.update(
                    [r["frota"] for r in rows if r.get("frota") is not None])
            novos = total - len(existing_frotas)
            atualizados = len(existing_frotas)
            c1, c2, c3 = st.columns(3)
            c1.metric("Linhas no CSV", total)
            c2.metric("Novos", novos)
            c3.metric("Atualizações", atualizados)
        except Exception:
            c1, c2 = st.columns(2)
            c1.metric("Linhas no CSV", total)
            c2.metric("Diagnóstico", "indisponível")

        if st.button(
            "Importar / Atualizar",
            type="primary",
            use_container_width=True,
            disabled=(
                len(df) == 0)):
            # Se vierem departamento/grupo no CSV, criamos/atualizamos
            # automaticamente
            dep_map = {}
            grp_map = {}
            try:
                if "departamento" in df.columns:
                    dep_map = _ensure_departamentos(
                        sb, tenant_id, df["departamento"].tolist())

                if "grupo" in df.columns:
                    # Suporta o mesmo nome de grupo em departamentos
                    # diferentes.
                    grupo_pairs: list[tuple[str, str | None]] = []
                    if "departamento" in df.columns:
                        for _, rr in df[["grupo", "departamento"]].fillna(
                                "").iterrows():
                            g = str(rr["grupo"]).strip()
                            d = str(rr["departamento"]).strip()
                            if not g:
                                continue
                            dep_id = dep_map.get(d.lower()) if d else None
                            grupo_pairs.append((g, dep_id))
                    else:
                        for g in df["grupo"].fillna("").tolist():
                            gg = str(g).strip()
                            if gg:
                                grupo_pairs.append((gg, None))

                    grp_map = _ensure_grupos(sb, tenant_id, grupo_pairs)
            except Exception:
                # Se der erro na criação automática, seguimos importando sem
                # grupo/departamento.
                dep_map = {}
                grp_map = {}

            payload = []
            for _, r in df.iterrows():
                item = {
                    "tenant_id": tenant_id,
                    "frota": str(
                        r["frota"]).strip()}
                for c in OPTIONAL_COLS:
                    if c in df.columns:
                        v = r.get(c)
                        if pd.isna(v):
                            continue
                        if c == "ano":
                            iv = _safe_int(v)
                            if iv is None:
                                continue
                            item[c] = iv
                        else:
                            item[c] = str(v).strip()

                # Grupo do CSV -> grupo_id do equipamento
                if "grupo" in df.columns:
                    gname = str(r.get("grupo") or "").strip()
                    if gname:
                        dep_id = None
                        if "departamento" in df.columns:
                            dname = str(r.get("departamento") or "").strip()
                            if dname:
                                dep_id = dep_map.get(dname.lower())

                        gid = grp_map.get((dep_id, gname.lower()))
                        # fallback: se não achou com dep_id, tenta sem
                        # departamento
                        if not gid:
                            gid = grp_map.get((None, gname.lower()))
                        if gid:
                            item["grupo_id"] = gid
                payload.append(item)

            try:
                sb.table("equipamentos").upsert(
                    payload, on_conflict="tenant_id,frota").execute()
                _audit(sb, tenant_id, "import", {"rows": len(payload)})
                st.success(
                    f"Importação concluída. Registros processados: {
                        len(payload)}")
                _rerun()
            except APIError as e:
                try:
                    detail = e.json()
                except Exception:
                    detail = {"message": str(e)}
                st.error("Erro ao importar via PostgREST.")
                st.json(detail)
            except Exception as e:
                st.error(f"Erro ao importar: {e}")

# ----- TAB 2: Organizar / Remanejar + Edição inline + Lixeira
