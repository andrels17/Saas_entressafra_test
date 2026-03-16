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
    options = [
        (item["id"], item.get("nome", "—"))
        for item in departamentos
        if item.get("id") and (not allowed_ids or item["id"] in allowed_ids)
    ]
    label_to_id = {name: dep_id for dep_id, name in options}
    selected_names = st.multiselect(
        label,
        options=[name for _, name in options],
        key=_normalize_key(key, "filter_departamentos"),
    )
    return [label_to_id[name] for name in selected_names if name in label_to_id]



def multiselect_grupos(
    grupos: list[dict],
    *,
    label: str = "Grupo",
    key: str | None = None,
    allowed_group_ids: list[str] | None = None,
    departamento_ids: list[str] | None = None,
) -> list[str]:
    options = []
    for grupo in grupos:
        grupo_id = grupo.get("id")
        if not grupo_id:
            continue
        if allowed_group_ids and grupo_id not in allowed_group_ids:
            continue
        if departamento_ids and grupo.get("departamento_id") not in departamento_ids:
            continue
        options.append((grupo_id, grupo.get("nome", "—")))

    label_to_id = {name: group_id for group_id, name in options}
    selected_names = st.multiselect(
        label,
        options=[name for _, name in options],
        key=_normalize_key(key, "filter_grupos"),
    )
    return [label_to_id[name] for name in selected_names if name in label_to_id]



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
