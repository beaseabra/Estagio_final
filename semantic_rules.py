# ===== semantic_rules.py =====

import re


SEMANTIC_NAME_FIXES = {
    "macchinacnc": "MaquinaCNC",
    "sensoriot": "SensorIoT",
    "manutencoaopreventiva": "ManutencaoPreventiva",

    "clientes": "Cliente",
    "produtos": "Produto",
    "encomendas": "Encomenda",
    "usuarios": "Utilizador",
    "usuario": "Utilizador",

    "estoque": "Stock",
    "cadastro": "Registo",
}


SEMANTIC_FIELD_FIXES = {
    "cpf": "nif",
    "cnpj": "nif_empresa",
    "usuario": "utilizador",
    "estoque": "stock",
}


# =========================
# SAFE REPLACE (🔥 IMPORTANTE)
# =========================

def safe_replace(text, old, new):
    return re.sub(rf"\b{re.escape(old)}\b", new, text)


# =========================
# OBJECT NAME FIX
# =========================

def fix_object_name(name: str):
    if not name:
        return name

    return SEMANTIC_NAME_FIXES.get(
        name.lower(),
        name
    )


# =========================
# FIELD NAME FIX
# =========================

def fix_field_name(name: str):
    if not name:
        return name

    return SEMANTIC_FIELD_FIXES.get(
        name.lower(),
        name.lower()
    )


# =========================
# APPLY RULES
# =========================

def apply_semantic_rules(schema: dict):

    if not schema:
        return schema

    object_rename_map = {}

    # =========================
    # OBJECTS
    # =========================

    for obj in schema.get("objects", []):
        old_name = obj.get("name")

        new_name = fix_object_name(old_name)

        object_rename_map[old_name] = new_name
        obj["name"] = new_name

    # =========================
    # FIELDS
    # =========================

    for obj in schema.get("objects", []):
        for field in obj.get("fields", []):

            fname = field.get("name")

            if not fname:
                continue

            # 🔥 proteger PKs
            if fname.endswith("id"):
                field["name"] = fname.lower()
                continue

            fixed_field = fix_field_name(fname)

            # 🔥 SAFE replace por palavras completas
            for old_obj, new_obj in object_rename_map.items():
                fixed_field = safe_replace(
                    fixed_field,
                    old_obj.lower(),
                    new_obj.lower()
                )

            field["name"] = fixed_field.lower()

    # =========================
    # RELATIONS
    # =========================

    for rel in schema.get("relations", []):
        rel["from"] = object_rename_map.get(rel.get("from"), rel.get("from"))
        rel["to"] = object_rename_map.get(rel.get("to"), rel.get("to"))

    # =========================
    # WORKSPACES
    # =========================

    for ws in schema.get("workspaces", []):

        ws["objects"] = [
            object_rename_map.get(o, o)
            for o in ws.get("objects", [])
        ]

        if "primary_entity" in ws:
            ws["primary_entity"] = object_rename_map.get(
                ws["primary_entity"],
                ws["primary_entity"]
            )

    # =========================
    # ACTIONS
    # =========================

    for action in schema.get("actions", []):

        for key in ["target", "from", "to", "object"]:
            if key in action:
                action[key] = object_rename_map.get(
                    action[key],
                    action[key]
                )

        if "entities_involved" in action:
            action["entities_involved"] = [
                object_rename_map.get(e, e)
                for e in action["entities_involved"]
            ]

    print("[semantic_rules] regras aplicadas")
