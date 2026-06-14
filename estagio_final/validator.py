# ===== validator.py =====

import re
from config import VALID_FIELD_TYPES


VALID_ACTION_TYPES = {
    "DOMAIN_ACTION",
    "CRUD_ACTION",
    "REPORT_ACTION",
    "NOTIFICATION_ACTION",
    "VALIDATION_ACTION",
    "INTEGRATION_ACTION",
    "AUTOMATED_ACTION",
    # Legacy
    "CREATE_OBJECT",
    "CREATE_RELATION",
    "ASSIGN_TO_WORKSPACE"
}

VALID_RELATION_TYPES = {"ONE_TO_MANY", "MANY_TO_MANY"}
VALID_TRIGGERS = {"manual", "automated", "scheduled"}


# =========================
# HARD VALIDATION
# =========================

def hard_validate(blueprint: dict) -> dict:
    errors = []
    warnings = []

    objects = blueprint.get("objects", [])
    relations = blueprint.get("relations", [])
    workspaces = blueprint.get("workspaces", [])
    actions = blueprint.get("actions", [])

    # --- RULE H1: Blueprint must not be empty ---
    if not objects:
        return {
            "passed": False,
            "errors": ["Blueprint has no objects"],
            "fixed_blueprint": blueprint
        }

    # --- 🔥 CHANGE: DO NOT BLOCK SMALL SYSTEMS ---
    if len(objects) < 2:
        warnings.append(f"Too few objects: {len(objects)}")

    # =========================
    # OBJECTS
    # =========================

    fixed_objects = []
    object_names = set()

    for i, obj in enumerate(objects):
        name = obj.get("name", "").strip()
        if not name:
            errors.append(f"Object at index {i} has no name")
            continue

        if name in object_names:
            continue

        object_names.add(name)

        fields = obj.get("fields", [])
        if not isinstance(fields, list):
            fields = []

        pk_name = name.lower() + "id"

        has_pk = any(
            f.get("name", "").lower() == pk_name
            for f in fields if isinstance(f, dict)
        )

        if not has_pk:
            fields.insert(0, {"name": pk_name, "type": "integer"})

        fixed_fields = []
        seen_field_names = set()

        for field in fields:
            if not isinstance(field, dict):
                continue

            fname = field.get("name", "").strip().lower()
            if not fname or fname in seen_field_names:
                continue

            seen_field_names.add(fname)

            ftype = field.get("type", "string").lower()
            if ftype not in VALID_FIELD_TYPES:
                ftype = _infer_type(fname)

            fixed_fields.append({"name": fname, "type": ftype})

        fixed_objects.append({"name": name, "fields": fixed_fields})

    # =========================
    # RELATIONS
    # =========================

    fixed_relations = []
    seen_relation_pairs = set()

    for rel in relations:
        if not isinstance(rel, dict):
            continue

        from_obj = rel.get("from", "").strip()
        to_obj = rel.get("to", "").strip()
        rel_type = rel.get("type", "ONE_TO_MANY").strip().upper()

        if not from_obj or not to_obj:
            continue

        if from_obj not in object_names or to_obj not in object_names:
            continue

        if rel_type not in VALID_RELATION_TYPES:
            rel_type = "ONE_TO_MANY"

        pair = (from_obj, to_obj)
        if pair in seen_relation_pairs:
            continue

        seen_relation_pairs.add(pair)

        fixed_relations.append({
            "from": from_obj,
            "to": to_obj,
            "type": rel_type,
            "label": rel.get("label", "")
        })

    # =========================
    # 🔥 AUTO-FIX CRITICAL RELATIONS
    # =========================

    names = {o["name"] for o in fixed_objects}

    def _add_relation(a, b):
        fixed_relations.append({
            "from": a,
            "to": b,
            "type": "ONE_TO_MANY",
            "label": ""
        })

    if "Encomenda" in names and "Cliente" in names:
        if not any(r["from"] == "Encomenda" and r["to"] == "Cliente" for r in fixed_relations):
            _add_relation("Encomenda", "Cliente")

    if "Encomenda" in names and "Produto" in names:
        if not any(r["from"] == "Encomenda" and r["to"] == "Produto" for r in fixed_relations):
            _add_relation("Encomenda", "Produto")

    # =========================
    # 🔥 CONNECT SUPPORT ENTITIES
    # =========================

    for obj in fixed_objects:
        name = obj["name"].lower()

        if any(k in name for k in ["log", "historico", "config"]):
            main = fixed_objects[0]["name"]

            if not any(r["to"] == obj["name"] for r in fixed_relations):
                fixed_relations.append({
                    "from": main,
                    "to": obj["name"],
                    "type": "ONE_TO_MANY",
                    "label": ""
                })

    # =========================
    # WORKSPACES
    # =========================

    fixed_workspaces = []
    seen_ws_names = set()

    for ws in workspaces:
        if not isinstance(ws, dict):
            continue

        name = ws.get("name", "").strip()
        if not name or name in seen_ws_names:
            continue

        seen_ws_names.add(name)

        ws_objects = [
            o for o in ws.get("objects", [])
            if o in object_names
        ]

        fixed_workspaces.append({
            "name": name,
            "description": ws.get("description", ""),
            "icon": ws.get("icon", "grid"),
            "color": ws.get("color", "#6B7280"),
            "objects": list(dict.fromkeys(ws_objects)),
            "primary_entity": ws.get("primary_entity", ws_objects[0] if ws_objects else ""),
            "permissions": ws.get("permissions", ["view", "create", "edit", "delete"])
        })

    # =========================
    # ACTIONS
    # =========================

    fixed_actions = []
    seen_action_names = set()

    for action in actions:
        if not isinstance(action, dict):
            continue

        name = action.get("name", "").strip()
        if not name or name in seen_action_names:
            continue

        seen_action_names.add(name)

        action_type = action.get("type", "DOMAIN_ACTION")
        if action_type not in VALID_ACTION_TYPES:
            action_type = "DOMAIN_ACTION"

        trigger = action.get("trigger", "manual")
        if trigger not in VALID_TRIGGERS:
            trigger = "manual"

        fixed_actions.append({
            "name": name,
            "type": action_type,
            "description": action.get("description", ""),
            "trigger": trigger,
            "entities_involved": [
                e for e in action.get("entities_involved", [])
                if e in object_names
            ],
            "steps": action.get("steps", []),
            "preconditions": action.get("preconditions", []),
            "postconditions": action.get("postconditions", [])
        })

    fixed_blueprint = {
        "objects": fixed_objects,
        "relations": fixed_relations,
        "actions": fixed_actions,
        "workspaces": fixed_workspaces,
        "metadata": blueprint.get("metadata", {})
    }

    passed = len(errors) == 0
    print(f"[validator:hard] {'PASSED' if passed else 'FAILED'}")

    return {
        "passed": passed,
        "errors": errors,
        "warnings": warnings,
        "fixed_blueprint": fixed_blueprint
    }


# =========================
# SOFT VALIDATION
# =========================

def soft_validate(blueprint: dict, prompt: str = "") -> dict:
    warnings = []
    suggestions = []

    objects = blueprint.get("objects", [])
    relations = blueprint.get("relations", [])
    actions = blueprint.get("actions", [])
    workspaces = blueprint.get("workspaces", [])

    object_names = {o["name"] for o in objects}

    # 🔥 ACTION QUALITY CHECK
    for action in actions:
        if not action.get("steps"):
            warnings.append(f"Action '{action['name']}' has no steps")

        if len(action.get("steps", [])) < 2:
            suggestions.append(f"Enrich action '{action['name']}' with more steps")

    # 🔥 ORPHAN OBJECTS
    connected = set()
    for rel in relations:
        connected.add(rel["from"])
        connected.add(rel["to"])

    for obj in object_names:
        if obj not in connected and len(objects) > 2:
            warnings.append(f"Object '{obj}' is isolated")

    # 🔥 WORKSPACE CHECK
    if not workspaces:
        warnings.append("No workspaces defined")

    # 🔥 COMPLEXITY
    complexity = min(len(objects) * 5 + len(relations) * 3 + len(actions), 100)

    return {
        "warnings": warnings,
        "suggestions": suggestions,
        "complexity_score": complexity
    }


# =========================
# MAIN
# =========================

def validate_and_fix(blueprint: dict, prompt: str = "") -> dict:
    hard = hard_validate(blueprint)
    fixed = hard["fixed_blueprint"]

    soft = soft_validate(fixed, prompt)

    fixed["_validation"] = {
        "hard": hard,
        "soft": soft
    }

    print("[validator] complete")
    return fixed


# =========================
# TYPE INFERENCE
# =========================

def _infer_type(field_name: str) -> str:
    fn = field_name.lower()

    if any(k in fn for k in ["preco", "valor", "total"]):
        return "float"
    if "id" in fn:
        return "integer"
    if "data" in fn:
        return "datetime"
    if "ativo" in fn:
        return "boolean"

    return "string"
