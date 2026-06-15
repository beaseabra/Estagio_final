# ===== handlers/update_schema_handler.py =====
# AiBizCore — Pydantic v2
#
# Responsabilidades:
#   1. Recebe o prompt do utilizador + o schema ATUAL (da cache).
#   2. Chama o Ollama (Llama 3B local) para gerar o diff de operações.
#   3. Valida o diff com a firewall _validate_diff() antes de aplicar qualquer mutação.
#   4. Aplica as mutações cirúrgicas (add / remove / rename / retype) de forma segura.
#   5. Garante referential integrity: se um Object é apagado, sai também dos Workspaces.
#   6. Toda a entrada e saída passa pela armadura parse_blueprint() do models.py.

from __future__ import annotations

import json
import logging
import re
import requests
from typing import Any, Dict, List, Optional, Tuple

from models import (
    BlueprintModel,
    FieldModel,
    ObjectModel,
    RelationModel,
    WorkspaceModel,
    parse_blueprint,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuração do Ollama
# ---------------------------------------------------------------------------

OLLAMA_URL   = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "llama3"          # ajusta se usares outro nome de modelo
OLLAMA_TIMEOUT = 120             # segundos — o Llama 3B pode ser lento


# ---------------------------------------------------------------------------
# Tipos de operação suportados no diff
# ---------------------------------------------------------------------------
# O LLM deve devolver um JSON com a chave "operations", lista de dicts:
#
#  { "op": "ADD_OBJECT",      "object": { <ObjectModel dict> } }
#  { "op": "REMOVE_OBJECT",   "name": "cliente" }
#  { "op": "RENAME_OBJECT",   "old_name": "cliente", "new_name": "customer" }
#  { "op": "ADD_FIELD",       "object": "cliente",  "field": { <FieldModel dict> } }
#  { "op": "REMOVE_FIELD",    "object": "cliente",  "field_name": "telefone" }
#  { "op": "RENAME_FIELD",    "object": "cliente",  "old_name": "tel", "new_name": "phone" }
#  { "op": "RETYPE_FIELD",    "object": "cliente",  "field_name": "id", "new_type": "UUID" }
#  { "op": "ADD_RELATION",    "relation": { <RelationModel dict> } }
#  { "op": "REMOVE_RELATION", "name": "cliente_encomenda" }
#  { "op": "ADD_WORKSPACE",   "workspace": { <WorkspaceModel dict> } }
#  { "op": "REMOVE_WORKSPACE","name": "backoffice" }
#  { "op": "ADD_TO_WORKSPACE","workspace": "backoffice", "object": "cliente" }
#  { "op": "REMOVE_FROM_WORKSPACE", "workspace": "backoffice", "object": "cliente" }

VALID_OPS = {
    "ADD_OBJECT",
    "REMOVE_OBJECT",
    "RENAME_OBJECT",
    "ADD_FIELD",
    "REMOVE_FIELD",
    "RENAME_FIELD",
    "RETYPE_FIELD",
    "ADD_RELATION",
    "REMOVE_RELATION",
    "ADD_WORKSPACE",
    "REMOVE_WORKSPACE",
    "ADD_TO_WORKSPACE",
    "REMOVE_FROM_WORKSPACE",
}


# ---------------------------------------------------------------------------
# PROMPT TEMPLATE para o Ollama
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are a JSON diff generator for a business schema system.
You receive a user instruction and the current schema (JSON).
You must return ONLY a valid JSON object with a single key "operations".
"operations" is a list of surgical changes to apply to the schema.

RULES (NEVER break these):
- Return ONLY raw JSON. No markdown, no explanation, no ```json fences.
- Objects have "fields" (list). Workspaces have "objects" (list of names). Never mix them.
- A Workspace is NOT an Object. Never put a Workspace inside "operations" as an ADD_OBJECT.
- A Relation has "from_object", "to_object", "type". Never put it inside ADD_OBJECT.
- For ADD_FIELD, "field" must be a single dict, never a list.
- Valid "op" values: ADD_OBJECT, REMOVE_OBJECT, RENAME_OBJECT, ADD_FIELD, REMOVE_FIELD,
  RENAME_FIELD, RETYPE_FIELD, ADD_RELATION, REMOVE_RELATION, ADD_WORKSPACE,
  REMOVE_WORKSPACE, ADD_TO_WORKSPACE, REMOVE_FROM_WORKSPACE.

EXAMPLE OUTPUT:
{"operations": [{"op": "ADD_FIELD", "object": "cliente", "field": {"name": "telefone", "type": "String"}}]}
"""

def _build_ollama_prompt(user_prompt: str, current_schema: Dict[str, Any]) -> str:
    """Constrói o prompt final que vai para o Ollama."""
    schema_str = json.dumps(current_schema, ensure_ascii=False, indent=2)
    return (
        f"{_SYSTEM_PROMPT}\n\n"
        f"CURRENT SCHEMA:\n{schema_str}\n\n"
        f"USER INSTRUCTION: {user_prompt}\n\n"
        f"JSON DIFF:"
    )


# ---------------------------------------------------------------------------
# CHAMADA AO OLLAMA
# ---------------------------------------------------------------------------

def _call_ollama(prompt_text: str) -> str:
    """
    Envia o prompt ao Ollama e devolve o texto bruto da resposta.
    Lança RuntimeError se a chamada falhar.
    """
    payload = {
        "model":  OLLAMA_MODEL,
        "prompt": prompt_text,
        "stream": False,
        "options": {
            "temperature": 0.1,    # baixo para respostas mais determinísticas
            "num_predict": 1024,
        },
    }

    try:
        resp = requests.post(OLLAMA_URL, json=payload, timeout=OLLAMA_TIMEOUT)
        resp.raise_for_status()
    except requests.exceptions.ConnectionError:
        raise RuntimeError(
            f"[update_schema_handler] Ollama não está acessível em {OLLAMA_URL}. "
            "Verifica se o serviço está em execução."
        )
    except requests.exceptions.Timeout:
        raise RuntimeError(
            f"[update_schema_handler] Timeout ao chamar o Ollama ({OLLAMA_TIMEOUT}s)."
        )
    except requests.exceptions.HTTPError as e:
        raise RuntimeError(f"[update_schema_handler] Ollama devolveu erro HTTP: {e}")

    data = resp.json()
    raw_text = data.get("response") or ""
    logger.debug("[update_schema_handler] Ollama raw response: %s", raw_text[:500])
    return raw_text


def _parse_llm_json(raw_text: str) -> Dict[str, Any]:
    """
    Extrai e faz parse do JSON da resposta bruta do LLM.
    Tenta várias estratégias para lidar com alucinações comuns:
      - texto com ```json ... ``` à volta
      - texto com lixo antes/depois do JSON
      - JSON válido direto
    """
    # Estratégia 1: parse direto
    try:
        return json.loads(raw_text.strip())
    except json.JSONDecodeError:
        pass

    # Estratégia 2: extrair bloco ```json ... ``` se o modelo ignorou as instruções
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw_text, re.DOTALL)
    if fence_match:
        try:
            return json.loads(fence_match.group(1))
        except json.JSONDecodeError:
            pass

    # Estratégia 3: encontrar o primeiro { ... } balanceado no texto
    brace_match = re.search(r"\{.*\}", raw_text, re.DOTALL)
    if brace_match:
        try:
            return json.loads(brace_match.group(0))
        except json.JSONDecodeError:
            pass

    raise ValueError(
        f"[update_schema_handler] Não foi possível extrair JSON válido da resposta do LLM.\n"
        f"Raw (primeiros 300 chars): {raw_text[:300]}"
    )


# ---------------------------------------------------------------------------
# FIREWALL — _validate_diff
# ---------------------------------------------------------------------------

class DiffValidationError(ValueError):
    """Erro lançado quando o diff da IA viola as regras estruturais."""
    pass


def _validate_diff(operations: List[Dict[str, Any]]) -> Tuple[bool, List[str]]:
    """
    Firewall rigorosa que verifica CADA operação antes de tocar no schema.

    Retorna (ok: bool, errors: List[str]).
    Se ok=False, NENHUMA mutação deve ser aplicada.
    """
    errors: List[str] = []

    if not isinstance(operations, list):
        return False, ["'operations' must be a list, got: " + type(operations).__name__]

    for i, op_dict in enumerate(operations):
        if not isinstance(op_dict, dict):
            errors.append(f"[op #{i}] Not a dict: {op_dict!r}")
            continue

        op = op_dict.get("op")
        if not op:
            errors.append(f"[op #{i}] Missing 'op' key.")
            continue
        if op not in VALID_OPS:
            errors.append(f"[op #{i}] Unknown operation '{op}'.")
            continue

        # ── REGRA CRÍTICA: Workspaces não podem aparecer dentro de Objects ──
        if op == "ADD_OBJECT":
            obj_payload = op_dict.get("object") or {}
            if not isinstance(obj_payload, dict):
                errors.append(f"[op #{i}] ADD_OBJECT.object must be a dict.")
                continue
            if "objects" in obj_payload and "fields" not in obj_payload:
                errors.append(
                    f"[op #{i}] ADD_OBJECT payload looks like a Workspace "
                    f"(has 'objects' key, no 'fields'). Refusing."
                )
            if "from_object" in obj_payload or "to_object" in obj_payload:
                errors.append(
                    f"[op #{i}] ADD_OBJECT payload looks like a Relation. Refusing."
                )

        # ── REGRA CRÍTICA: Objects não podem aparecer dentro de Workspaces ──
        if op == "ADD_WORKSPACE":
            ws_payload = op_dict.get("workspace") or {}
            if not isinstance(ws_payload, dict):
                errors.append(f"[op #{i}] ADD_WORKSPACE.workspace must be a dict.")
                continue
            if "fields" in ws_payload:
                errors.append(
                    f"[op #{i}] ADD_WORKSPACE payload has 'fields' — looks like an Object. Refusing."
                )

        # ── Validações de campos obrigatórios por op ──
        required_keys: Dict[str, List[str]] = {
            "REMOVE_OBJECT":         ["name"],
            "RENAME_OBJECT":         ["old_name", "new_name"],
            "ADD_FIELD":             ["object", "field"],
            "REMOVE_FIELD":          ["object", "field_name"],
            "RENAME_FIELD":          ["object", "old_name", "new_name"],
            "RETYPE_FIELD":          ["object", "field_name", "new_type"],
            "ADD_RELATION":          ["relation"],
            "REMOVE_RELATION":       ["name"],
            "REMOVE_WORKSPACE":      ["name"],
            "ADD_TO_WORKSPACE":      ["workspace", "object"],
            "REMOVE_FROM_WORKSPACE": ["workspace", "object"],
        }

        if op in required_keys:
            for key in required_keys[op]:
                if key not in op_dict:
                    errors.append(f"[op #{i}] '{op}' missing required key '{key}'.")

        # ── ADD_FIELD: o payload 'field' não pode ser uma lista ──
        if op == "ADD_FIELD":
            field_payload = op_dict.get("field")
            if isinstance(field_payload, list):
                errors.append(
                    f"[op #{i}] ADD_FIELD.field is a list instead of a single field dict. "
                    f"Use multiple ADD_FIELD operations."
                )

    ok = len(errors) == 0
    return ok, errors


# ---------------------------------------------------------------------------
# HELPERS de mutação
# ---------------------------------------------------------------------------

def _find_object(bp: BlueprintModel, name: str) -> Optional[ObjectModel]:
    name_lower = name.lower()
    for obj in bp.objects:
        if obj.name.lower() == name_lower:
            return obj
    return None


def _find_workspace(bp: BlueprintModel, name: str) -> Optional[WorkspaceModel]:
    name_lower = name.lower()
    for ws in bp.workspaces:
        if ws.name.lower() == name_lower:
            return ws
    return None


def _remove_object_from_all_workspaces(bp: BlueprintModel, object_name: str) -> None:
    """Remove referências a um Object apagado em TODOS os Workspaces."""
    name_lower = object_name.lower()
    for ws in bp.workspaces:
        ws.objects = [o for o in ws.objects if o.lower() != name_lower]


def _rename_object_in_workspaces(bp: BlueprintModel, old_name: str, new_name: str) -> None:
    """Atualiza referências de um Object renomeado em todos os Workspaces."""
    old_lower = old_name.lower()
    for ws in bp.workspaces:
        ws.objects = [
            new_name if o.lower() == old_lower else o
            for o in ws.objects
        ]


def _rename_object_in_relations(bp: BlueprintModel, old_name: str, new_name: str) -> None:
    """Atualiza from_object/to_object nas Relations quando um Object é renomeado."""
    old_lower = old_name.lower()
    for rel in bp.relations:
        if rel.from_object.lower() == old_lower:
            rel.from_object = new_name
        if rel.to_object.lower() == old_lower:
            rel.to_object = new_name


def _remove_object_from_relations(bp: BlueprintModel, object_name: str) -> None:
    """Remove todas as Relations que referenciam um Object apagado."""
    name_lower = object_name.lower()
    bp.relations = [
        r for r in bp.relations
        if r.from_object.lower() != name_lower and r.to_object.lower() != name_lower
    ]


# ---------------------------------------------------------------------------
# APLICAÇÃO DE OPERAÇÕES
# ---------------------------------------------------------------------------

def _apply_operation(bp: BlueprintModel, op_dict: Dict[str, Any]) -> List[str]:
    """
    Aplica UMA operação ao BlueprintModel (mutação in-place).
    Retorna lista de warnings (não fatais).
    """
    op = op_dict["op"]
    warnings: List[str] = []

    # ── ADD_OBJECT ──────────────────────────────────────────────────────────
    if op == "ADD_OBJECT":
        payload = op_dict.get("object") or {}
        try:
            new_obj = ObjectModel(**payload) if payload else ObjectModel(name="unnamed")
        except Exception as e:
            warnings.append(f"ADD_OBJECT: could not parse object payload — {e}")
            return warnings

        if _find_object(bp, new_obj.name):
            warnings.append(f"ADD_OBJECT: object '{new_obj.name}' already exists. Skipped.")
        else:
            bp.objects.append(new_obj)

    # ── REMOVE_OBJECT ───────────────────────────────────────────────────────
    elif op == "REMOVE_OBJECT":
        name = op_dict["name"]
        original_count = len(bp.objects)
        name_lower = name.lower()
        bp.objects = [o for o in bp.objects if o.name.lower() != name_lower]

        if len(bp.objects) == original_count:
            warnings.append(f"REMOVE_OBJECT: '{name}' not found.")
        else:
            _remove_object_from_all_workspaces(bp, name)
            _remove_object_from_relations(bp, name)

    # ── RENAME_OBJECT ───────────────────────────────────────────────────────
    elif op == "RENAME_OBJECT":
        old_name = op_dict["old_name"]
        new_name = op_dict["new_name"]
        obj = _find_object(bp, old_name)
        if obj is None:
            warnings.append(f"RENAME_OBJECT: '{old_name}' not found.")
        elif _find_object(bp, new_name):
            warnings.append(f"RENAME_OBJECT: target name '{new_name}' already exists. Skipped.")
        else:
            obj.name = new_name
            _rename_object_in_workspaces(bp, old_name, new_name)
            _rename_object_in_relations(bp, old_name, new_name)

    # ── ADD_FIELD ───────────────────────────────────────────────────────────
    elif op == "ADD_FIELD":
        obj_name = op_dict["object"]
        field_payload = op_dict["field"]
        obj = _find_object(bp, obj_name)

        if obj is None:
            warnings.append(f"ADD_FIELD: object '{obj_name}' not found.")
        else:
            try:
                new_field = (
                    FieldModel(**field_payload)
                    if isinstance(field_payload, dict)
                    else FieldModel(name=str(field_payload))
                )
            except Exception as e:
                warnings.append(f"ADD_FIELD: invalid field payload — {e}")
                return warnings

            existing_names = {f.name.lower() for f in obj.fields}
            if new_field.name.lower() in existing_names:
                warnings.append(
                    f"ADD_FIELD: field '{new_field.name}' already exists in '{obj_name}'. Skipped."
                )
            else:
                obj.fields.append(new_field)

    # ── REMOVE_FIELD ────────────────────────────────────────────────────────
    elif op == "REMOVE_FIELD":
        obj_name   = op_dict["object"]
        field_name = op_dict["field_name"]
        obj = _find_object(bp, obj_name)

        if obj is None:
            warnings.append(f"REMOVE_FIELD: object '{obj_name}' not found.")
        else:
            original_count = len(obj.fields)
            fn_lower = field_name.lower()
            obj.fields = [f for f in obj.fields if f.name.lower() != fn_lower]
            if len(obj.fields) == original_count:
                warnings.append(f"REMOVE_FIELD: field '{field_name}' not found in '{obj_name}'.")

    # ── RENAME_FIELD ────────────────────────────────────────────────────────
    elif op == "RENAME_FIELD":
        obj_name = op_dict["object"]
        old_name = op_dict["old_name"]
        new_name = op_dict["new_name"]
        obj = _find_object(bp, obj_name)

        if obj is None:
            warnings.append(f"RENAME_FIELD: object '{obj_name}' not found.")
        else:
            old_lower = old_name.lower()
            target_field = next((f for f in obj.fields if f.name.lower() == old_lower), None)
            if target_field is None:
                warnings.append(f"RENAME_FIELD: field '{old_name}' not found in '{obj_name}'.")
            else:
                new_lower = new_name.lower()
                collision = any(
                    f.name.lower() == new_lower and f is not target_field
                    for f in obj.fields
                )
                if collision:
                    warnings.append(
                        f"RENAME_FIELD: '{new_name}' already exists in '{obj_name}'. Skipped."
                    )
                else:
                    target_field.name = new_name

    # ── RETYPE_FIELD ────────────────────────────────────────────────────────
    elif op == "RETYPE_FIELD":
        obj_name   = op_dict["object"]
        field_name = op_dict["field_name"]
        new_type   = op_dict["new_type"]
        obj = _find_object(bp, obj_name)

        if obj is None:
            warnings.append(f"RETYPE_FIELD: object '{obj_name}' not found.")
        else:
            fn_lower = field_name.lower()
            target_field = next((f for f in obj.fields if f.name.lower() == fn_lower), None)
            if target_field is None:
                warnings.append(f"RETYPE_FIELD: field '{field_name}' not found in '{obj_name}'.")
            else:
                target_field.type = str(new_type)

    # ── ADD_RELATION ────────────────────────────────────────────────────────
    elif op == "ADD_RELATION":
        rel_payload = op_dict.get("relation") or {}
        try:
            new_rel = RelationModel(**rel_payload) if isinstance(rel_payload, dict) else None
        except Exception as e:
            warnings.append(f"ADD_RELATION: invalid payload — {e}")
            return warnings

        if new_rel is None:
            warnings.append("ADD_RELATION: empty relation payload.")
        else:
            if not _find_object(bp, new_rel.from_object):
                warnings.append(f"ADD_RELATION: from_object '{new_rel.from_object}' not found.")
            elif not _find_object(bp, new_rel.to_object):
                warnings.append(f"ADD_RELATION: to_object '{new_rel.to_object}' not found.")
            else:
                existing_names = {r.name.lower() for r in bp.relations}
                if new_rel.name.lower() in existing_names:
                    warnings.append(
                        f"ADD_RELATION: relation '{new_rel.name}' already exists. Skipped."
                    )
                else:
                    bp.relations.append(new_rel)

    # ── REMOVE_RELATION ─────────────────────────────────────────────────────
    elif op == "REMOVE_RELATION":
        name = op_dict["name"]
        original_count = len(bp.relations)
        name_lower = name.lower()
        bp.relations = [r for r in bp.relations if r.name.lower() != name_lower]
        if len(bp.relations) == original_count:
            warnings.append(f"REMOVE_RELATION: '{name}' not found.")

    # ── ADD_WORKSPACE ───────────────────────────────────────────────────────
    elif op == "ADD_WORKSPACE":
        payload = op_dict.get("workspace") or {}
        try:
            new_ws = (
                WorkspaceModel(**payload)
                if isinstance(payload, dict)
                else WorkspaceModel(name="unnamed_ws")
            )
        except Exception as e:
            warnings.append(f"ADD_WORKSPACE: invalid payload — {e}")
            return warnings

        if _find_workspace(bp, new_ws.name):
            warnings.append(f"ADD_WORKSPACE: workspace '{new_ws.name}' already exists. Skipped.")
        else:
            valid_names  = {o.name.lower() for o in bp.objects}
            original_refs = new_ws.objects[:]
            new_ws.objects = [o for o in new_ws.objects if o.lower() in valid_names]
            dropped = set(original_refs) - set(new_ws.objects)
            if dropped:
                warnings.append(f"ADD_WORKSPACE: removed unknown object refs: {dropped}")
            bp.workspaces.append(new_ws)

    # ── REMOVE_WORKSPACE ────────────────────────────────────────────────────
    elif op == "REMOVE_WORKSPACE":
        name = op_dict["name"]
        original_count = len(bp.workspaces)
        name_lower = name.lower()
        bp.workspaces = [w for w in bp.workspaces if w.name.lower() != name_lower]
        if len(bp.workspaces) == original_count:
            warnings.append(f"REMOVE_WORKSPACE: '{name}' not found.")

    # ── ADD_TO_WORKSPACE ────────────────────────────────────────────────────
    elif op == "ADD_TO_WORKSPACE":
        ws_name  = op_dict["workspace"]
        obj_name = op_dict["object"]
        ws  = _find_workspace(bp, ws_name)
        obj = _find_object(bp, obj_name)

        if ws is None:
            warnings.append(f"ADD_TO_WORKSPACE: workspace '{ws_name}' not found.")
        elif obj is None:
            warnings.append(f"ADD_TO_WORKSPACE: object '{obj_name}' not found.")
        else:
            if obj.name not in ws.objects:
                ws.objects.append(obj.name)

    # ── REMOVE_FROM_WORKSPACE ───────────────────────────────────────────────
    elif op == "REMOVE_FROM_WORKSPACE":
        ws_name  = op_dict["workspace"]
        obj_name = op_dict["object"]
        ws = _find_workspace(bp, ws_name)

        if ws is None:
            warnings.append(f"REMOVE_FROM_WORKSPACE: workspace '{ws_name}' not found.")
        else:
            obj_lower      = obj_name.lower()
            original_count = len(ws.objects)
            ws.objects     = [o for o in ws.objects if o.lower() != obj_lower]
            if len(ws.objects) == original_count:
                warnings.append(
                    f"REMOVE_FROM_WORKSPACE: '{obj_name}' not found in '{ws_name}'."
                )

    else:
        warnings.append(f"Unknown operation '{op}' — skipped.")

    return warnings


# ---------------------------------------------------------------------------
# HANDLER PRINCIPAL
# ---------------------------------------------------------------------------

def handle_update_schema(
    prompt: str,
    current_schema: Dict[str, Any],
    *,
    strict: bool = True,
) -> Dict[str, Any]:
    """
    Entry point do handler de atualização de schema.

    Parâmetros
    ----------
    prompt : str
        A frase que o utilizador escreveu (ex: "Adiciona o campo email ao cliente").
    current_schema : dict
        O schema atual vindo da cache (pode ser bruto da IA — será blindado).
    strict : bool
        Se True (padrão), qualquer erro da firewall aborta TODAS as mutações.
        Se False, aplica as operações válidas e descarta as inválidas.

    Retorno
    -------
    dict com:
        "blueprint"  : schema final blindado pelo parse_blueprint
        "applied"    : lista de ops aplicadas com sucesso
        "skipped"    : lista de ops ignoradas
        "errors"     : lista de erros fatais (vazio se success=True)
        "warnings"   : avisos acumulados durante a aplicação
        "success"    : bool
    """

    # ── 1. Blindar o schema atual antes de qualquer coisa ────────────────────
    bp: BlueprintModel = parse_blueprint(
        current_schema if isinstance(current_schema, dict) else {}
    )

    print(f"[update_schema_handler] prompt='{prompt}'")
    print(f"[update_schema_handler] schema tem {len(bp.objects)} objetos, "
          f"{len(bp.relations)} relações, {len(bp.workspaces)} workspaces")

    # ── 2. Chamar o Ollama para gerar o diff ─────────────────────────────────
    ollama_prompt = _build_ollama_prompt(prompt, bp.to_dict())

    try:
        raw_llm_response = _call_ollama(ollama_prompt)
    except RuntimeError as e:
        logger.error(str(e))
        return _error_response(bp, [str(e)])

    # ── 3. Fazer parse seguro do JSON devolvido pelo LLM ────────────────────
    try:
        diff = _parse_llm_json(raw_llm_response)
    except ValueError as e:
        msg = str(e)
        logger.error(msg)
        return _error_response(bp, [msg])

    # ── 4. Extrair lista de operações ────────────────────────────────────────
    operations: Any = diff.get("operations")
    if operations is None:
        # Compatibilidade: LLM devolveu uma única op sem a chave "operations"
        if "op" in diff:
            operations = [diff]
        else:
            return _error_response(bp, [
                "O LLM não devolveu a chave 'operations'. "
                f"JSON recebido: {json.dumps(diff)[:300]}"
            ])

    # ── 5. FIREWALL ──────────────────────────────────────────────────────────
    ok, firewall_errors = _validate_diff(operations)

    if not ok and strict:
        logger.warning(
            "[update_schema_handler] Firewall BLOQUEOU o diff. Erros: %s", firewall_errors
        )
        return _error_response(bp, firewall_errors)

    if not ok and not strict:
        logger.warning(
            "[update_schema_handler] Strict=False — a aplicar apenas ops válidas. "
            "Erros ignorados: %s", firewall_errors
        )
        clean_ops = []
        for op_dict in operations:
            single_ok, _ = _validate_diff([op_dict])
            if single_ok:
                clean_ops.append(op_dict)
        operations = clean_ops

    # ── 6. Deep copy defensivo — não mutar o schema original se algo correr mal ──
    bp_copy: BlueprintModel = parse_blueprint(bp.to_dict())

    applied: List[str]     = []
    all_warnings: List[str] = []

    # ── 7. Aplicar operações uma a uma ──────────────────────────────────────
    for op_dict in operations:
        op_label = op_dict.get("op", "UNKNOWN")
        try:
            op_warnings = _apply_operation(bp_copy, op_dict)
            all_warnings.extend(op_warnings)
            applied.append(op_label)
        except Exception as exc:
            msg = f"[{op_label}] Erro inesperado: {exc}"
            logger.exception(msg)
            all_warnings.append(msg)
            if strict:
                return _error_response(bp, [msg])

    # ── 8. Blindar a saída final ─────────────────────────────────────────────
    final_blueprint = parse_blueprint(bp_copy.to_dict())

    print(
        f"[update_schema_handler] Concluído — {len(applied)} ops aplicadas | "
        f"objects={len(final_blueprint.objects)} | "
        f"relations={len(final_blueprint.relations)} | "
        f"workspaces={len(final_blueprint.workspaces)}"
    )

    # ALTERA ESTE RETORNO PARA FICAR ASSIM:
    return {
        "success": True,
        "type": "SYSTEM",
        "data": final_blueprint.to_dict(), # Mudança de "blueprint" para "data"
        "mutations_applied": len(applied),  # Adiciona isto
        "mutation_log": applied,           # Adiciona isto
        "struct_warnings": all_warnings,    # Adiciona isto
        "errors": firewall_errors if not ok else []
    }


# ---------------------------------------------------------------------------
# HELPERS internos
# ---------------------------------------------------------------------------

def _error_response(bp: BlueprintModel, errors: List[str]) -> Dict[str, Any]:
    """Resposta de erro — devolve o schema ORIGINAL intacto."""
    # ALTERA ESTE RETORNO PARA FICAR ASSIM:
    return {
        "success": False,
        "type": "SYSTEM",
        "data": bp.to_dict(),        # Mudança de "blueprint" para "data"
        "mutations_applied": 0,      # Adiciona isto
        "mutation_log": [],          # Adiciona isto
        "struct_warnings": [],       # Adiciona isto
        "errors": errors
    }
