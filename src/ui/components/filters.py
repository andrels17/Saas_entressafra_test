from __future__ import annotations

import streamlit as st


def filter_shell():
    return st.container()


def filter_caption(text: str):
    st.markdown(
        f'<div class="ds-filter-caption">{text}</div>',
        unsafe_allow_html=True,
    )


def filter_hint(text: str):
    st.caption(text)


def _normalize_key(key: str | None, fallback: str) -> str:
    return key or fallback


def select_revisao(
    revisoes: list[dict],
    *,
    label: str = "Revisão",
    key: str | None = None,
    default_status: str = "ativa",
    show_status_icon: bool = True,
) -> dict | None:
    if not revisoes:
        return None

    status_icon = {"ativa": "🟢", "fechada": "⚪", "arquivada": "🗄️"}
    labels: list[str] = []
    label_map: dict[str, dict] = {}

    for revisao in revisoes:
        status = str(revisao.get("status") or "")
        prefix = f"{status_icon.get(status, '○')} " if show_status_icon else ""
        item_label = f"{prefix}{revisao.get('titulo', 'Sem título')} [{status}]"
        labels.append(item_label)
        label_map[item_label] = revisao

    default_idx = next(
        (i for i, r in enumerate(revisoes) if r.get("status") == default_status),
        0,
    )
    selected_label = st.selectbox(
        label,
        labels,
        index=default_idx,
        key=_normalize_key(key, "filter_revisao"),
    )
    return label_map[selected_label]


def multiselect_departamentos(
    departamentos: list[dict],
    *,
    label: str = "Departamento",
    key: str | None = None,
    allowed_ids: list[str] | None = None,
) -> list[str]:
    options: list[tuple[str, str]] = []
    allowed = {str(x) for x in allowed_ids} if allowed_ids is not None else None

    for item in departamentos:
        dep_id = item.get("id")
        if not dep_id:
            continue
        dep_id = str(dep_id)
        if allowed is not None and dep_id not in allowed:
            continue
        nome = str(item.get("nome", "—"))
        display = f"{nome} ({dep_id[:8]})"
        options.append((display, dep_id))

    label_to_id = {display: dep_id for display, dep_id in options}
    selected_labels = st.multiselect(
        label,
        options=[display for display, _ in options],
        key=_normalize_key(key, "filter_departamentos"),
    )
    return [label_to_id[label] for label in selected_labels if label in label_to_id]



def multiselect_grupos(
    grupos: list[dict],
    *,
    label: str = "Grupo",
    key: str | None = None,
    allowed_group_ids: list[str] | None = None,
    departamento_ids: list[str] | None = None,
) -> list[str]:
    options: list[tuple[str, str]] = []
    allowed_groups = {str(x) for x in allowed_group_ids} if allowed_group_ids is not None else None
    allowed_depts = {str(x) for x in departamento_ids} if departamento_ids else None

    for grupo in grupos:
        grupo_id = grupo.get("id")
        if not grupo_id:
            continue
        grupo_id = str(grupo_id)
        grupo_dep_id = str(grupo.get("departamento_id")) if grupo.get("departamento_id") else None

        if allowed_groups is not None and grupo_id not in allowed_groups:
            continue
        if allowed_depts and grupo_dep_id not in allowed_depts:
            continue

        nome = str(grupo.get("nome", "—"))
        display = f"{nome} ({grupo_id[:8]})"
        options.append((display, grupo_id))

    label_to_id = {display: group_id for display, group_id in options}
    selected_labels = st.multiselect(
        label,
        options=[display for display, _ in options],
        key=_normalize_key(key, "filter_grupos"),
    )
    return [label_to_id[label] for label in selected_labels if label in label_to_id]



def select_grupo(
    grupos: list[dict],
    *,
    label: str = "Grupo",
    key: str | None = None,
    default_id: str | None = None,
) -> tuple[str | None, str | None]:
    options = [(item["nome"], item["id"]) for item in grupos if item.get("id")]
    if not options:
        return None, None

    names = [name for name, _ in options]
    name_to_id = {name: item_id for name, item_id in options}
    default_name = next((name for name, item_id in options if item_id == default_id), names[0])
    selected_name = st.selectbox(
        label,
        names,
        index=names.index(default_name),
        key=_normalize_key(key, "filter_grupo"),
    )
    return selected_name, name_to_id[selected_name]



def select_equipamento(
    equipamentos: list[dict],
    *,
    label: str = "Equipamento",
    key: str | None = None,
    default_id: str | None = None,
) -> tuple[str | None, str | None]:
    options: list[tuple[str, str]] = []
    for equipamento in equipamentos:
        equipamento_id = equipamento.get("id")
        if not equipamento_id:
            continue
        frota = str(equipamento.get("frota") or "").strip()
        modelo = str(equipamento.get("modelo") or "").strip()
        item_label = f"{frota} — {modelo}".strip(" —") or str(equipamento_id)
        options.append((item_label, equipamento_id))

    if not options:
        return None, None

    labels = [item_label for item_label, _ in options]
    label_to_id = {item_label: item_id for item_label, item_id in options}
    default_label = next(
        (item_label for item_label, item_id in options if item_id == default_id),
        labels[0],
    )
    selected_label = st.selectbox(
        label,
        labels,
        index=labels.index(default_label),
        key=_normalize_key(key, "filter_equipamento"),
    )
    return selected_label, label_to_id[selected_label]
