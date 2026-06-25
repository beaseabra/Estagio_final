# ===== validator.py =====

import re
import unicodedata

from config import VALID_FIELD_TYPES


VALID_ACTION_TYPES = {
    "DOMAIN_ACTION",
    "CRUD_ACTION",
    "REPORT_ACTION",
    "NOTIFICATION_ACTION",
    "VALIDATION_ACTION",
    "INTEGRATION_ACTION",
    "AUTOMATED_ACTION",
    "CREATE_OBJECT",
    "CREATE_RELATION",
    "ASSIGN_TO_WORKSPACE"
}

VALID_RELATION_TYPES = {"ONE_TO_MANY", "MANY_TO_MANY"}
VALID_TRIGGERS = {"manual", "automated", "scheduled"}


DOMAIN_RELATION_SPECS = [
    ("Cliente", "Encomenda", "ONE_TO_MANY"),
    ("Encomenda", "Produto", "MANY_TO_MANY"),
    ("Encomenda", "Pagamento", "ONE_TO_MANY"),
    ("Categoria", "Produto", "ONE_TO_MANY"),
    ("Fornecedor", "Produto", "MANY_TO_MANY"),
    ("Paciente", "Consulta", "ONE_TO_MANY"),
    ("Medico", "Consulta", "ONE_TO_MANY"),
    ("Médico", "Consulta", "ONE_TO_MANY"),
    ("Aluno", "Inscricao", "ONE_TO_MANY"),
    ("Aluno", "Inscrição", "ONE_TO_MANY"),
    ("Curso", "Inscricao", "ONE_TO_MANY"),
    ("Curso", "Inscrição", "ONE_TO_MANY"),
    ("Cliente", "Conta", "ONE_TO_MANY"),
    ("Conta", "Transacao", "ONE_TO_MANY"),
    ("Conta", "Transação", "ONE_TO_MANY"),
]


SUPPORT_KEYWORDS = {
    "log",
    "logsistema",
    "historico",
    "config",
    "configuracao"
}


# =========================
# UTILS
# =========================

def _strip_accents(text: str) -> str:
    text = str(text)
    normalized = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")


def _canonical_token(name: str) -> str:
    clean = _strip_accents(str(name).lower())
    return re.sub(r"[^a-z0-9]", "", clean)


def _normalize_field_name(name: str) -> str:
    name = _strip_accents(str(name).strip().lower())
    name = re.sub(r"[\s\-]+", "_", name)
    name = re.sub(r"[^a-z0-9_]", "", name)
    return name


def _fix_common_field_name(name: str) -> str:
    fixes = {
        "preo": "preco",
        "mtodo": "metodo",
        "descrio": "descricao",
        "criadoem": "created_at",
        "atualizadoem": "updated_at"
    }

    return fixes.get(name, name)


def _as_list(value):
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]

    if isinstance(value, str) and value.strip():
        return [value.strip()]

    return []


def _normalize_action_type(value: str) -> str:
    raw = str(value or "").strip().upper()
    normalized = _strip_accents(raw)

    aliases = {
        "ACAO DE DOMINIO": "DOMAIN_ACTION",
        "AÇÃO DE DOMÍNIO": "DOMAIN_ACTION",
        "OPERACAO BASICA": "CRUD_ACTION",
        "OPERAÇÃO BÁSICA": "CRUD_ACTION",
        "CRUD": "CRUD_ACTION",
        "RELATORIO": "REPORT_ACTION",
        "RELATÓRIO": "REPORT_ACTION",
        "NOTIFICACAO": "NOTIFICATION_ACTION",
        "NOTIFICAÇÃO": "NOTIFICATION_ACTION",
        "VALIDACAO": "VALIDATION_ACTION",
        "VALIDAÇÃO": "VALIDATION_ACTION",
        "INTEGRACAO": "INTEGRATION_ACTION",
        "INTEGRAÇÃO": "INTEGRATION_ACTION",
        "AUTOMACAO": "AUTOMATED_ACTION",
        "AUTOMAÇÃO": "AUTOMATED_ACTION",
    }

    if raw in VALID_ACTION_TYPES:
        return raw

    if normalized in VALID_ACTION_TYPES:
        return normalized

    return aliases.get(normalized, "DOMAIN_ACTION")


def _normalize_trigger(value: str) -> str:
    raw = _strip_accents(str(value or "").strip().lower())

    aliases = {
        "manual": "manual",
        "automated": "automated",
        "automatico": "automated",
        "automatizado": "automated",
        "scheduled": "scheduled",
        "agendado": "scheduled",
        "recorrente": "scheduled",
    }

    return aliases.get(raw, "manual")


def _is_support_entity(name: str) -> bool:
    token = _canonical_token(name)
    return any(k in token for k in SUPPORT_KEYWORDS)


def _is_known_domain_context(tokens: set) -> bool:
    ecommerce = {"cliente", "produto", "encomenda", "pagamento"}
    hospital = {"paciente", "consulta"}
    education = {"aluno", "curso"}
    finance = {"cliente", "conta"}

    return (
        len(ecommerce.intersection(tokens)) >= 3
        or len(hospital.intersection(tokens)) >= 2
        or len(education.intersection(tokens)) >= 2
        or len(finance.intersection(tokens)) >= 2
    )


def _choose_main_entity(object_names: list) -> str:
    priority = [
        "Encomenda",
        "Pedido",
        "Consulta",
        "Processo",
        "Projeto",
        "Conta",
        "Cliente",
        "Produto"
    ]

    available = set(object_names)

    for name in priority:
        if name in available:
            return name

    for name in object_names:
        if not _is_support_entity(name):
            return name

    return object_names[0] if object_names else ""


def _canonicalize_relation(from_obj: str, to_obj: str, rel_type: str, name_by_token: dict):
    from_token = _canonical_token(from_obj)
    to_token = _canonical_token(to_obj)

    for left, right, canonical_type in DOMAIN_RELATION_SPECS:
        left_token = _canonical_token(left)
        right_token = _canonical_token(right)

        if left_token not in name_by_token or right_token not in name_by_token:
            continue

        if {from_token, to_token} == {left_token, right_token}:
            return (
                name_by_token[left_token],
                name_by_token[right_token],
                canonical_type,
                True
            )

    return from_obj, to_obj, rel_type, False


def _add_relation(fixed_relations: list, seen_pairs: set, from_obj: str, to_obj: str, rel_type: str, label: str = ""):
    if not from_obj or not to_obj or from_obj == to_obj:
        return

    if rel_type not in VALID_RELATION_TYPES:
        rel_type = "ONE_TO_MANY"

    pair = (from_obj, to_obj)
    reverse_pair = (to_obj, from_obj)

    if pair in seen_pairs:
        return

    if reverse_pair in seen_pairs:
        return

    seen_pairs.add(pair)

    fixed_relations.append({
        "from": from_obj,
        "to": to_obj,
        "type": rel_type,
        "label": label
    })


# =========================
# TYPE INFERENCE
# =========================

def _infer_type(field_name: str) -> str:
    fn = field_name.lower()

    if any(k in fn for k in ["preco", "valor", "total", "custo", "iva", "saldo"]):
        return "float"

    if any(k in fn for k in ["id", "numero", "quantidade", "stock", "count"]):
        return "integer"

    if "data" in fn or "date" in fn:
        return "datetime"

    if any(k in fn for k in ["ativo", "flag", "estado", "validado"]):
        return "boolean"

    if any(k in fn for k in ["descricao", "observacoes", "notas"]):
        return "text"

    return "string"


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

    if not objects:
        return {
            "passed": False,
            "errors": ["Blueprint has no objects"],
            "warnings": warnings,
            "fixed_blueprint": blueprint
        }

    fixed_objects = []
    object_names = set()

    for i, obj in enumerate(objects):
        if not isinstance(obj, dict):
            continue

        name = str(obj.get("name", "")).strip()

        if not name:
            errors.append(f"Object at index {i} has no name")
            continue

        if name in object_names:
            continue

        object_names.add(name)

        fields = obj.get("fields", [])
        if not isinstance(fields, list):
            fields = []

        pk_name = _canonical_token(name) + "id"

        has_pk = any(
            _fix_common_field_name(_normalize_field_name(f.get("name", ""))) == pk_name
            for f in fields
            if isinstance(f, dict)
        )

        if not has_pk:
            fields.insert(0, {"name": pk_name, "type": "integer"})

        fixed_fields = []
        seen_field_names = set()

        for field in fields:
            if not isinstance(field, dict):
                continue

            fname = _normalize_field_name(field.get("name", ""))
            fname = _fix_common_field_name(fname)

            if not fname or fname in seen_field_names:
                continue

            seen_field_names.add(fname)

            ftype = str(field.get("type", "string")).lower().strip()

            if ftype not in VALID_FIELD_TYPES:
                ftype = _infer_type(fname)

            if fname == "preco":
                ftype = "float"

            if fname == "metodo":
                ftype = "string"

            fixed_fields.append({
                "name": fname,
                "type": ftype
            })

        fixed_objects.append({
            "name": name,
            "fields": fixed_fields
        })

    object_names = {o["name"] for o in fixed_objects}
    object_names_list = [o["name"] for o in fixed_objects]

    name_by_token = {
        _canonical_token(name): name
        for name in object_names
    }

    known_context = _is_known_domain_context(set(name_by_token.keys()))

    # =========================
    # RELATIONS
    # =========================

    fixed_relations = []
    seen_relation_pairs = set()

    for rel in relations:
        if not isinstance(rel, dict):
            continue

        from_obj = str(rel.get("from", "")).strip()
        to_obj = str(rel.get("to", "")).strip()
        rel_type = str(rel.get("type", "ONE_TO_MANY")).strip().upper()
        label = str(rel.get("label", "")).strip()

        if not from_obj or not to_obj:
            continue

        if from_obj not in object_names or to_obj not in object_names:
            continue

        if rel_type not in VALID_RELATION_TYPES:
            rel_type = "ONE_TO_MANY"

        from_obj, to_obj, rel_type, is_canonical = _canonicalize_relation(
            from_obj,
            to_obj,
            rel_type,
            name_by_token
        )

        if _is_support_entity(from_obj) or _is_support_entity(to_obj):
            continue

        if known_context and not is_canonical:
            continue

        _add_relation(
            fixed_relations,
            seen_relation_pairs,
            from_obj,
            to_obj,
            rel_type,
            label
        )

    # Auto-fix das relações canónicas
    for left, right, rel_type in DOMAIN_RELATION_SPECS:
        left_token = _canonical_token(left)
        right_token = _canonical_token(right)

        if left_token in name_by_token and right_token in name_by_token:
            _add_relation(
                fixed_relations,
                seen_relation_pairs,
                name_by_token[left_token],
                name_by_token[right_token],
                rel_type,
                ""
            )

    # Relações controladas para entidades de suporte
    main_entity = _choose_main_entity(object_names_list)

    historico = name_by_token.get("historico")
    logsistema = name_by_token.get("logsistema")
    configuracao = name_by_token.get("configuracao")

    if main_entity and historico and main_entity != historico:
        _add_relation(
            fixed_relations,
            seen_relation_pairs,
            main_entity,
            historico,
            "ONE_TO_MANY",
            ""
        )

    if main_entity and logsistema and main_entity != logsistema:
        _add_relation(
            fixed_relations,
            seen_relation_pairs,
            main_entity,
            logsistema,
            "ONE_TO_MANY",
            ""
        )

    if configuracao and logsistema and configuracao != logsistema:
        _add_relation(
            fixed_relations,
            seen_relation_pairs,
            configuracao,
            logsistema,
            "ONE_TO_MANY",
            ""
        )

    # =========================
    # WORKSPACES
    # =========================

    fixed_workspaces = []
    seen_ws_names = set()

    for ws in workspaces:
        if not isinstance(ws, dict):
            continue

        name = str(ws.get("name", "")).strip()

        if not name or name in seen_ws_names:
            continue

        seen_ws_names.add(name)

        raw_ws_objects = ws.get("objects", [])
        if not isinstance(raw_ws_objects, list):
            raw_ws_objects = []

        ws_objects = [
            o for o in raw_ws_objects
            if o in object_names
        ]

        primary_entity = ws.get("primary_entity", "")
        if primary_entity not in object_names:
            primary_entity = ws_objects[0] if ws_objects else ""

        permissions = ws.get("permissions", ["VER", "CRIAR", "EDITAR", "APAGAR"])
        if not isinstance(permissions, list):
            permissions = ["VER"]

        fixed_workspaces.append({
            "name": name,
            "description": str(ws.get("description", "")).strip(),
            "icon": str(ws.get("icon", "grid")).strip() or "grid",
            "color": str(ws.get("color", "#6B7280")).strip() or "#6B7280",
            "objects": list(dict.fromkeys(ws_objects)),
            "primary_entity": primary_entity,
            "permissions": list(dict.fromkeys([str(p).strip() for p in permissions if str(p).strip()]))
        })

    # =========================
    # ACTIONS
    # =========================

    fixed_actions = []
    seen_action_names = set()

    for action in actions:
        if not isinstance(action, dict):
            continue

        name = str(action.get("name", "")).strip()

        if not name or name in seen_action_names:
            continue

        seen_action_names.add(name)

        action_type = _normalize_action_type(action.get("type", "DOMAIN_ACTION"))
        trigger = _normalize_trigger(action.get("trigger", "manual"))

        entities_involved = [
            e for e in _as_list(action.get("entities_involved", []))
            if e in object_names
        ]

        if not entities_involved:
            low_name = name.lower()
            for obj_name in object_names:
                if obj_name.lower() in low_name:
                    entities_involved.append(obj_name)

        fixed_actions.append({
            "name": name,
            "type": action_type,
            "description": str(action.get("description", "")).strip(),
            "trigger": trigger,
            "entities_involved": list(dict.fromkeys(entities_involved)),
            "steps": _as_list(action.get("steps", [])),
            "preconditions": _as_list(action.get("preconditions", [])),
            "postconditions": _as_list(action.get("postconditions", []))
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

    object_names = {o["name"] for o in objects if isinstance(o, dict)}

    for action in actions:
        if not isinstance(action, dict):
            continue

        action_name = action.get("name", "AçãoSemNome")
        steps = action.get("steps", [])

        if not steps:
            warnings.append(f"Action '{action_name}' has no steps")

        if len(steps) < 2:
            suggestions.append(f"Enrich action '{action_name}' with more steps")

    connected = set()

    for rel in relations:
        if not isinstance(rel, dict):
            continue

        connected.add(rel.get("from"))
        connected.add(rel.get("to"))

    for obj in object_names:
        if obj not in connected and len(objects) > 2:
            warnings.append(f"Object '{obj}' is isolated")

    if not workspaces:
        warnings.append("No workspaces defined")

    complexity = min(
        len(objects) * 5
        + len(relations) * 3
        + len(actions),
        100
    )

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
    fixed = hard.get("fixed_blueprint", blueprint)

    soft = soft_validate(fixed, prompt)

    hard_summary = {
        "passed": hard.get("passed", False),
        "errors": hard.get("errors", []),
        "warnings": hard.get("warnings", []),
        "fixed_blueprint": None
    }

    fixed["_validation"] = {
        "hard": hard_summary,
        "soft": soft
    }

    print("[validator] complete")
    return fixed
