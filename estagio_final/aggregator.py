# ===== aggregator.py =====

from normalizer import (
    normalize_object_name,
    normalize_field_name,
    normalize_workspace_name
)


def aggregate_blueprint(
    objects_data: dict,
    relations_data: dict,
    actions_data: dict,
    workspaces_data: dict
) -> dict:

    # =========================
    # FAILSAFE DEFAULTS
    # =========================

    objects_data = objects_data or {}
    relations_data = relations_data or {"relations": []}
    actions_data = actions_data or {"actions": []}
    workspaces_data = workspaces_data or {"workspaces": []}

    # 🔥 SUPORTAR entities do planner
    if "entities" in objects_data:
        raw_objects = [
            {
                "name": e["name"],
                "fields": [{"name": f} for f in e.get("suggested_fields", [])]
            }
            for e in objects_data["entities"]
        ]
    else:
        raw_objects = objects_data.get("objects", [])

    raw_relations = relations_data.get("relations", [])
    raw_actions = actions_data.get("actions", [])
    raw_workspaces = workspaces_data.get("workspaces", [])

    # =========================
    # OBJECTS
    # =========================

    object_map = {}

    for obj in raw_objects:
        if not isinstance(obj, dict):
            continue

        name = normalize_object_name(obj.get("name", ""))
        if not name:
            continue

        fields = []
        seen = set()

        for f in obj.get("fields", []):
            fname = normalize_field_name(f.get("name", ""))
            if not fname or fname in seen:
                continue
            seen.add(fname)
            fields.append({"name": fname, "type": f.get("type", "string")})

        if name not in object_map:
            object_map[name] = {"name": name, "fields": fields}
        else:
            existing = {f["name"] for f in object_map[name]["fields"]}
            for f in fields:
                if f["name"] not in existing:
                    object_map[name]["fields"].append(f)

    # =========================
    # RELATIONS
    # =========================

    normalized_relations = []

    for rel in raw_relations:
        from_obj = normalize_object_name(rel.get("from", ""))
        to_obj = normalize_object_name(rel.get("to", ""))
        rel_type = rel.get("type", "ONE_TO_MANY")

        if (
            not from_obj or not to_obj
            or from_obj == to_obj
            or from_obj not in object_map
            or to_obj not in object_map
        ):
            continue

        normalized_relations.append({
            "from": from_obj,
            "to": to_obj,
            "type": rel_type,
            "label": rel.get("label", "")
        })

    # =========================
    # FK INJECTION (CONTROLADO)
    # =========================

    for rel in normalized_relations:
        if rel["type"] != "ONE_TO_MANY":
            continue

        from_obj = rel["from"]
        to_obj = rel["to"]

        fk = normalize_field_name(f"ref_{to_obj.lower()}")

        existing = {f["name"] for f in object_map[from_obj]["fields"]}

        if fk not in existing:
            object_map[from_obj]["fields"].append({
                "name": fk,
                "type": "integer"
            })

    # =========================
    # MANY TO MANY
    # =========================

    for rel in normalized_relations:
        if rel["type"] != "MANY_TO_MANY":
            continue

        a, b = sorted([rel["from"], rel["to"]])
        junction = normalize_object_name(f"{a}{b}")

        if junction not in object_map:
            object_map[junction] = {
                "name": junction,
                "fields": [
                    {"name": f"{junction.lower()}id", "type": "integer"},
                    {"name": f"ref_{a.lower()}", "type": "integer"},
                    {"name": f"ref_{b.lower()}", "type": "integer"},
                    {"name": "created_at", "type": "datetime"}
                ]
            }

    # =========================
    # CLEAN OBJECTS
    # =========================

    cleaned_objects = []

    for obj in object_map.values():
        seen = set()
        fields = []

        for f in obj["fields"]:
            name = normalize_field_name(f["name"])
            if name and name not in seen:
                seen.add(name)
                fields.append({
                    "name": name,
                    "type": f.get("type", "string")
                })

        cleaned_objects.append({
            "name": obj["name"],
            "fields": fields
        })

    valid_names = {o["name"] for o in cleaned_objects}

    # =========================
    # WORKSPACES
    # =========================

    cleaned_workspaces = []

    for ws in raw_workspaces:
        name = normalize_workspace_name(ws.get("name", ""))
        if not name:
            continue

        objs = [
            normalize_object_name(o)
            for o in ws.get("objects", [])
            if normalize_object_name(o) in valid_names
        ]

        cleaned_workspaces.append({
            "name": name,
            "description": ws.get("description", ""),
            "objects": list(set(objs)),
            "primary_entity": objs[0] if objs else ""
        })

    # fallback workspace
    if not cleaned_workspaces:
        cleaned_workspaces.append({
            "name": "Geral",
            "description": "Sistema geral",
            "objects": list(valid_names),
            "primary_entity": list(valid_names)[0] if valid_names else ""
        })

    # =========================
    # ACTIONS (🔥 CORRIGIDO)
    # =========================

    cleaned_actions = []

    for action in raw_actions:
        name = action.get("name")
        if not name:
            continue

        entities = [
            normalize_object_name(e)
            for e in action.get("entities_involved", [])
            if normalize_object_name(e) in valid_names
        ]

        cleaned_actions.append({
            "name": name,
            "type": action.get("type", "DOMAIN_ACTION"),
            "entities_involved": entities,
            "description": action.get("description", ""),
            "trigger": action.get("trigger", "manual"), # 🔥 Copia o trigger
            "steps": action.get("steps", []),           # 🔥 Copia os steps
            "preconditions": action.get("preconditions", []), # 🔥 Copia pre-condições
            "postconditions": action.get("postconditions", []) # 🔥 Copia pós-condições
        })

    # fallback actions
    if not cleaned_actions:
        for obj in cleaned_objects:
            cleaned_actions.append({
                "name": f"Criar{obj['name']}",
                "type": "CRUD_ACTION",
                "entities_involved": [obj["name"]],
                "description": f"Criar {obj['name']}",
                "trigger": "manual",
                "steps": [
                    f"Validar permissões",
                    f"Criar registo de {obj['name']} na base de dados",
                    f"Registar log da operação"
                ],
                "preconditions": ["Utilizador autenticado"],
                "postconditions": [f"{obj['name']} criado com sucesso"]
            })

    # =========================
    # FINAL
    # =========================

    blueprint = {
        "objects": cleaned_objects,
        "relations": normalized_relations,
        "actions": cleaned_actions,
        "workspaces": cleaned_workspaces,
        "metadata": {
            "total_objects": len(cleaned_objects),
            "total_relations": len(normalized_relations),
            "total_actions": len(cleaned_actions),
            "total_workspaces": len(cleaned_workspaces)
        }
    }

    print(f"[aggregator] OK — {len(cleaned_objects)} objetos")

    return blueprint


def aggregate_schema(objects_data, relations_data, workspaces_data):
    return aggregate_blueprint(objects_data, relations_data, {}, workspaces_data)
