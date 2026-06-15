# ===== aggregator.py =====
# Refatorado para Pydantic v2 — AiBizCore
#
# MUDANÇA PRINCIPAL: Toda a lógica de agregação opera sobre modelos Pydantic
# internamente. O output final é um dict retrocompatível via .to_dict().
# Nenhum .get() sobre dados crus da IA — a blindagem acontece na entrada.

from __future__ import annotations

from typing import Any

from models import (
    BlueprintModel,
    ObjectModel,
    FieldModel,
    RelationModel,
    ActionModel,
    WorkspaceModel,
    parse_blueprint,
    _coerce_list,
    _safe_str,
)
from normalizer import (
    normalize_object_name,
    normalize_field_name,
    normalize_workspace_name,
)


def aggregate_blueprint(
    objects_data: Any,
    relations_data: Any,
    actions_data: Any,
    workspaces_data: Any,
) -> dict:
    """
    Agrega os outputs dos quatro generators num blueprint consolidado.

    Aceita dicts crus (possivelmente com nulls da IA).
    Devolve um dict retrocompatível com o pipeline legado.
    """

    # ── Normalizar entradas — nunca confiar em None ───────────────────────────
    objects_data   = objects_data   if isinstance(objects_data, dict)   else {}
    relations_data = relations_data if isinstance(relations_data, dict) else {}
    actions_data   = actions_data   if isinstance(actions_data, dict)   else {}
    workspaces_data = workspaces_data if isinstance(workspaces_data, dict) else {}

    # ─────────────────────────────────────────────
    # OBJECTS — suporte a formato "entities" do planner
    # ─────────────────────────────────────────────

    if "entities" in objects_data:
        raw_objects = [
            {
                "name": e.get("name", ""),
                "fields": [
                    {"name": f}
                    for f in _coerce_list(e.get("suggested_fields"))
                    if f  # filtrar strings vazias/None
                ],
            }
            for e in _coerce_list(objects_data.get("entities"))
            if isinstance(e, dict) and e.get("name")
        ]
    else:
        raw_objects = _coerce_list(objects_data.get("objects"))

    raw_relations  = _coerce_list(relations_data.get("relations"))
    raw_actions    = _coerce_list(actions_data.get("actions"))
    raw_workspaces = _coerce_list(workspaces_data.get("workspaces"))

    # ─────────────────────────────────────────────
    # OBJECTS — construir mapa normalizado
    # ─────────────────────────────────────────────

    object_map: dict[str, ObjectModel] = {}

    for raw_obj in raw_objects:
        if not isinstance(raw_obj, dict):
            continue

        name = normalize_object_name(_safe_str(raw_obj.get("name")))
        if not name:
            continue

        # Construir via Pydantic — absorve campos null, tipos errados, etc.
        obj_model = ObjectModel.model_validate({"name": name, "fields": raw_obj.get("fields")})

        if name not in object_map:
            object_map[name] = obj_model
        else:
            # Merge de campos (sem duplicatas — ObjectModel.deduplicate_fields trata)
            existing_field_names = {f.name for f in object_map[name].fields}
            for f in obj_model.fields:
                if f.name not in existing_field_names:
                    object_map[name].fields.append(f)

    # ─────────────────────────────────────────────
    # RELATIONS — normalizar e validar contra object_map
    # ─────────────────────────────────────────────

    normalized_relations: list[RelationModel] = []
    seen_pairs: set[tuple[str, str]] = set()

    for raw_rel in raw_relations:
        if not isinstance(raw_rel, dict):
            continue

        from_obj = normalize_object_name(_safe_str(raw_rel.get("from")))
        to_obj   = normalize_object_name(_safe_str(raw_rel.get("to")))

        if (
            not from_obj or not to_obj
            or from_obj == to_obj
            or from_obj not in object_map
            or to_obj not in object_map
            or (from_obj, to_obj) in seen_pairs
        ):
            continue

        seen_pairs.add((from_obj, to_obj))
        rel = RelationModel.model_validate({
            "from": from_obj,
            "to": to_obj,
            "type": raw_rel.get("type", "ONE_TO_MANY"),
            "label": raw_rel.get("label", ""),
        })
        normalized_relations.append(rel)

    # ─────────────────────────────────────────────
    # FK INJECTION — apenas ONE_TO_MANY
    # ─────────────────────────────────────────────

    for rel in normalized_relations:
        if rel.type != "ONE_TO_MANY":
            continue

        fk_name = normalize_field_name(f"ref_{rel.to_obj.lower()}")
        existing = {f.name for f in object_map[rel.from_obj].fields}

        if fk_name not in existing:
            object_map[rel.from_obj].fields.append(
                FieldModel(name=fk_name, type="integer")
            )
        else:
            print(
                f"[aggregator] INFO: FK '{fk_name}' já existia em "
                f"'{rel.from_obj}', injecção duplicada evitada."
            )

    # ─────────────────────────────────────────────
    # MANY-TO-MANY — junction tables
    # ─────────────────────────────────────────────

    for rel in normalized_relations:
        if rel.type != "MANY_TO_MANY":
            continue

        a, b = sorted([rel.from_obj, rel.to_obj])
        junction_name = normalize_object_name(f"{a}{b}")

        if junction_name not in object_map:
            object_map[junction_name] = ObjectModel.model_validate({
                "name": junction_name,
                "fields": [
                    {"name": f"{junction_name.lower()}id", "type": "integer"},
                    {"name": f"ref_{a.lower()}", "type": "integer"},
                    {"name": f"ref_{b.lower()}", "type": "integer"},
                    {"name": "created_at", "type": "datetime"},
                ],
            })

    # ─────────────────────────────────────────────
    # WORKSPACES
    # ─────────────────────────────────────────────

    valid_names = set(object_map.keys())
    cleaned_workspaces: list[WorkspaceModel] = []
    seen_ws: set[str] = set()

    for raw_ws in raw_workspaces:
        if not isinstance(raw_ws, dict):
            continue

        ws_name = normalize_workspace_name(_safe_str(raw_ws.get("name")))
        if not ws_name or ws_name in seen_ws:
            continue
        seen_ws.add(ws_name)

        # Filtrar objects do workspace que realmente existem no object_map
        ws_objects = [
            normalize_object_name(_safe_str(o))
            for o in _coerce_list(raw_ws.get("objects"))
            if normalize_object_name(_safe_str(o)) in valid_names
        ]

        ws_model = WorkspaceModel.model_validate({
            **raw_ws,
            "name": ws_name,
            "objects": ws_objects,
        })
        cleaned_workspaces.append(ws_model)

    # Fallback workspace se nenhum foi gerado
    if not cleaned_workspaces:
        all_names = list(valid_names)
        cleaned_workspaces.append(WorkspaceModel(
            name="Geral",
            description="Sistema geral",
            objects=all_names,
            primary_entity=all_names[0] if all_names else "",
        ))

    # ─────────────────────────────────────────────
    # ACTIONS
    # ─────────────────────────────────────────────

    cleaned_actions: list[ActionModel] = []
    seen_actions: set[str] = set()

    for raw_action in raw_actions:
        if not isinstance(raw_action, dict):
            continue

        act_name = _safe_str(raw_action.get("name"))
        if not act_name or act_name in seen_actions:
            continue
        seen_actions.add(act_name)

        # Filtrar entities_involved que existem
        entities = [
            normalize_object_name(_safe_str(e))
            for e in _coerce_list(raw_action.get("entities_involved"))
            if normalize_object_name(_safe_str(e)) in valid_names
        ]

        action_model = ActionModel.model_validate({
            **raw_action,
            "name": act_name,
            "entities_involved": entities,
        })
        cleaned_actions.append(action_model)

    # Fallback actions se nenhuma foi gerada
    if not cleaned_actions:
        for obj in object_map.values():
            cleaned_actions.append(ActionModel(
                name=f"Criar{obj.name}",
                type="CRUD_ACTION",
                description=f"Criar {obj.name}",
                trigger="manual",
                entities_involved=[obj.name],
                steps=[
                    "Validar permissões",
                    f"Criar registo de {obj.name} na base de dados",
                    "Registar log da operação",
                ],
                preconditions=["Utilizador autenticado"],
                postconditions=[f"{obj.name} criado com sucesso"],
            ))

    # ─────────────────────────────────────────────
    # MONTAR BlueprintModel FINAL
    # ─────────────────────────────────────────────

    blueprint = BlueprintModel(
        objects=list(object_map.values()),
        relations=normalized_relations,
        actions=cleaned_actions,
        workspaces=cleaned_workspaces,
    )

    print(f"[aggregator] OK — {len(blueprint.objects)} objetos")

    # Devolver dict retrocompatível (o pipeline legado espera dict)
    return blueprint.to_dict()


# Alias retrocompatível
def aggregate_schema(objects_data, relations_data, workspaces_data):
    return aggregate_blueprint(objects_data, relations_data, {}, workspaces_data)