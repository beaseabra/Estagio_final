# ===== handlers/update_schema_handler.py =====
# v4.5 — Update schema robusto para AiBizCore
#         Compatível com /api/generate + /api/chat
#         Parser robusto para outputs LLM (<think>, markdown, texto livre)
#         Operações determinísticas antes do LLM:
#         Objects, fields, relations, workspaces e actions

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

import requests

from models import (
    BlueprintModel,
    FieldModel,
    ObjectModel,
    RelationModel,
    WorkspaceModel,
    ActionModel,
    parse_blueprint,
)


logger = logging.getLogger("update_schema_handler")
logging.basicConfig(level=logging.INFO, format="[%(name)s] %(levelname)s — %(message)s")


# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURAÇÃO
# ─────────────────────────────────────────────────────────────────────────────

try:
    from config import MODELS, OPTIONS, OPTIONS_8B, OLLAMA_URL as _CFG_URL, MODEL_TIER

    OLLAMA_MODEL = MODELS.get("update_schema", MODELS.get("router", "llama3.2:3b"))
    OLLAMA_OPTIONS = OPTIONS_8B if MODEL_TIER == "8b" else OPTIONS
    OLLAMA_BASE = _CFG_URL
except ImportError:
    logger.warning("config.py não encontrado — a usar defaults")
    OLLAMA_MODEL = "llama3.2:3b"
    OLLAMA_OPTIONS = {"temperature": 0.1, "num_ctx": 2048, "num_predict": 1024}
    OLLAMA_BASE = "http://localhost:11434/api/generate"


_BASE = OLLAMA_BASE.replace("/api/generate", "").replace("/api/chat", "")
ENDPOINT_GENERATE = f"{_BASE}/api/generate"
ENDPOINT_CHAT = f"{_BASE}/api/chat"
OLLAMA_TIMEOUT = 180


# ─────────────────────────────────────────────────────────────────────────────
# OPERAÇÕES VÁLIDAS
# ─────────────────────────────────────────────────────────────────────────────

VALID_OPS = {
    "ADD_OBJECT", "REMOVE_OBJECT", "RENAME_OBJECT",
    "ADD_FIELD", "REMOVE_FIELD", "RENAME_FIELD", "RETYPE_FIELD",
    "ADD_RELATION", "REMOVE_RELATION", "UPDATE_RELATION_TYPE",
    "ADD_WORKSPACE", "REMOVE_WORKSPACE",
    "ADD_TO_WORKSPACE", "REMOVE_FROM_WORKSPACE",
    "ADD_ACTION", "REMOVE_ACTION", "RENAME_ACTION",
    "UPDATE_ACTION_TRIGGER", "UPDATE_ACTION_DESCRIPTION",
    "ADD_ENTITY_TO_ACTION", "REMOVE_ENTITY_FROM_ACTION",
}

VALID_FIELD_TYPES = {
    "string", "integer", "float", "boolean", "date", "datetime", "text"
}

VALID_RELATION_TYPES = {"ONE_TO_MANY", "MANY_TO_MANY"}

VALID_ACTION_TRIGGERS = {
    "manual", "automated", "scheduled",
    "automatizado", "agendado",
}


FEW_SHOT_EXAMPLES = """
=== EXEMPLOS (Few-Shot) ===

EXEMPLO 1 — Adicionar campo:
Input: "Adiciona campo preco ao objeto produto"
Output: {"operations": [{"op": "ADD_FIELD", "object": "produto", "field": {"name": "preco", "type": "float"}}]}

EXEMPLO 2 — Renomear objeto:
Input: "Renomeia cliente para utilizador"
Output: {"operations": [{"op": "RENAME_OBJECT", "old_name": "cliente", "new_name": "utilizador"}]}

EXEMPLO 3 — Adicionar objeto:
Input: "Cria o objeto Fornecedor com nome e email"
Output: {"operations": [{"op": "ADD_OBJECT", "object": {"name": "Fornecedor", "fields": [{"name": "nome", "type": "string"}, {"name": "email", "type": "string"}]}}]}

EXEMPLO 4 — Relação entre objetos:
Input: "Liga Cliente a Encomenda com ONE_TO_MANY"
Output: {"operations": [{"op": "ADD_RELATION", "relation": {"from": "Cliente", "to": "Encomenda", "type": "ONE_TO_MANY"}}]}

EXEMPLO 5 — Criar ação:
Input: "Cria uma ação AprovarTrabalho para o objeto Trabalho"
Output: {"operations": [{"op": "ADD_ACTION", "action": {"name": "AprovarTrabalho", "type": "DOMAIN_ACTION", "description": "AprovarTrabalho no sistema", "trigger": "manual", "entities_involved": ["Trabalho"], "steps": ["validar dados necessários", "executar ação", "confirmar resultado"], "preconditions": ["Trabalho deve existir"], "postconditions": ["ação concluída"]}}]}

=== FIM DOS EXEMPLOS ===
"""


_SYSTEM_PROMPT = f"""\
És um gerador de diffs JSON para um sistema de schemas de negócio.
Recebes uma instrução e o schema atual em JSON.
Devolves APENAS um objeto JSON válido com a chave "operations".

REGRAS ABSOLUTAS:
1. APENAS JSON puro. ZERO texto. ZERO explicações. ZERO markdown.
2. Não uses <think> ou qualquer bloco de raciocínio. Responde diretamente.
3. Objetos têm "fields". Workspaces têm "objects". NUNCA os confundas.
4. ADD_FIELD: "field" é sempre um dict único, nunca uma lista.
5. ADD_RELATION: o payload tem "from", "to", "type".
6. ADD_ACTION: o payload tem "name", "type", "description", "trigger", "entities_involved", "steps", "preconditions", "postconditions".
7. Verifica se o workspace/objeto/ação existe no schema antes de o referenciar.

OPERAÇÕES VÁLIDAS:
ADD_OBJECT, REMOVE_OBJECT, RENAME_OBJECT,
ADD_FIELD, REMOVE_FIELD, RENAME_FIELD, RETYPE_FIELD,
ADD_RELATION, REMOVE_RELATION,
ADD_WORKSPACE, REMOVE_WORKSPACE, ADD_TO_WORKSPACE, REMOVE_FROM_WORKSPACE,
ADD_ACTION, REMOVE_ACTION, RENAME_ACTION,
UPDATE_ACTION_TRIGGER, UPDATE_ACTION_DESCRIPTION,
ADD_ENTITY_TO_ACTION, REMOVE_ENTITY_FROM_ACTION

{FEW_SHOT_EXAMPLES}
"""


# ─────────────────────────────────────────────────────────────────────────────
# LIMPEZA/PARSER DE OUTPUT DO LLM
# ─────────────────────────────────────────────────────────────────────────────

def _strip_llm_noise(text: str) -> str:
    if not text:
        return ""

    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = text.replace("```", "")

    text = text.strip()
    start = text.find("{")
    end = text.rfind("}")

    if start != -1 and end != -1 and end > start:
        return text[start:end + 1].strip()

    return text


def _extract_balanced_json(text: str) -> Optional[str]:
    depth = 0
    start = None
    in_str = False
    escape = False

    for i, ch in enumerate(text):
        if escape:
            escape = False
            continue

        if ch == "\\" and in_str:
            escape = True
            continue

        if ch == '"':
            in_str = not in_str
            continue

        if in_str:
            continue

        if ch == "{":
            if depth == 0:
                start = i
            depth += 1

        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                return text[start:i + 1]

    return None


def _parse_llm_json(raw_text: str) -> Dict[str, Any]:
    if not raw_text or not raw_text.strip():
        raise ValueError("Resposta vazia do LLM.")

    candidates = [
        raw_text.strip(),
        _strip_llm_noise(raw_text),
    ]

    balanced = _extract_balanced_json(candidates[-1] or raw_text)
    if balanced:
        candidates.append(balanced)

    for candidate in candidates:
        if not candidate:
            continue
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

    brace_match = re.search(r"\{.*\}", candidates[-1] or raw_text, re.DOTALL)
    if brace_match:
        try:
            return json.loads(brace_match.group(0))
        except json.JSONDecodeError:
            pass

    raise ValueError(
        "Não foi possível extrair JSON válido da resposta do LLM. "
        f"Raw: {raw_text[:400]}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# OLLAMA
# ─────────────────────────────────────────────────────────────────────────────

def _try_generate(prompt_text: str, options: dict) -> Optional[str]:
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt_text,
        "stream": False,
        "options": options,
    }

    try:
        resp = requests.post(ENDPOINT_GENERATE, json=payload, timeout=OLLAMA_TIMEOUT)
        resp.raise_for_status()
        raw = resp.json().get("response") or ""

        if raw.strip():
            return _strip_llm_noise(raw)

        logger.warning("[generate] resposta vazia")
        return None

    except requests.exceptions.HTTPError as e:
        logger.warning("[generate] HTTP erro: %s", e)
        return None

    except requests.exceptions.ConnectionError:
        raise RuntimeError(
            f"Ollama inacessível em {ENDPOINT_GENERATE}. "
            "Verifica se está a correr: ollama serve"
        )

    except requests.exceptions.Timeout:
        raise RuntimeError(f"Timeout ({OLLAMA_TIMEOUT}s) em /api/generate.")

    except Exception as e:
        logger.warning("[generate] erro inesperado: %s", e)
        return None


def _try_chat(prompt_text: str, options: dict) -> Optional[str]:
    payload = {
        "model": OLLAMA_MODEL,
        "messages": [{"role": "user", "content": prompt_text}],
        "stream": False,
        "options": options,
    }

    try:
        resp = requests.post(ENDPOINT_CHAT, json=payload, timeout=OLLAMA_TIMEOUT)
        resp.raise_for_status()
        raw = (resp.json().get("message") or {}).get("content") or ""

        if raw.strip():
            return _strip_llm_noise(raw)

        logger.warning("[chat] resposta vazia")
        return None

    except requests.exceptions.HTTPError as e:
        logger.warning("[chat] HTTP erro: %s", e)
        return None

    except Exception as e:
        logger.warning("[chat] erro inesperado: %s", e)
        return None


def _call_ollama(prompt_text: str, temperature: Optional[float] = None) -> str:
    temp = temperature if temperature is not None else float(OLLAMA_OPTIONS.get("temperature", 0.1))
    options = {**OLLAMA_OPTIONS, "temperature": temp}

    result = _try_generate(prompt_text, options)
    if result is not None:
        return result

    logger.warning("/api/generate falhou ou devolveu vazio — a tentar /api/chat")

    result = _try_chat(prompt_text, options)
    if result is not None:
        return result

    raise RuntimeError(
        f"Ollama não respondeu em nenhum endpoint ({ENDPOINT_GENERATE}, {ENDPOINT_CHAT}). "
        f"Verifica se o modelo '{OLLAMA_MODEL}' está instalado."
    )


def _build_ollama_prompt(
    user_prompt: str,
    current_schema: Dict[str, Any],
    error_context: Optional[str] = None,
) -> str:
    schema_str = json.dumps(current_schema, ensure_ascii=False, indent=2)
    base = f"{_SYSTEM_PROMPT}\n\nSCHEMA ATUAL:\n{schema_str}\n\n"

    if error_context:
        base += (
            f"ÚLTIMA OPERAÇÃO FALHOU:\n{error_context}\n\n"
            "Corrige APENAS este erro e gera a operação correta.\n\n"
        )

    base += f"INSTRUÇÃO: {user_prompt}\n\nJSON DIFF:"
    return base


# ─────────────────────────────────────────────────────────────────────────────
# FIREWALL DE DIFF
# ─────────────────────────────────────────────────────────────────────────────

def _validate_diff(operations: List[Dict[str, Any]]) -> Tuple[bool, List[str]]:
    errors: List[str] = []

    if not isinstance(operations, list):
        return False, [f"'operations' deve ser lista, recebeu {type(operations).__name__}"]

    required: Dict[str, List[str]] = {
        "REMOVE_OBJECT": ["name"],
        "RENAME_OBJECT": ["old_name", "new_name"],
        "ADD_FIELD": ["object", "field"],
        "REMOVE_FIELD": ["object", "field_name"],
        "RENAME_FIELD": ["object", "old_name", "new_name"],
        "RETYPE_FIELD": ["object", "field_name", "new_type"],
        "ADD_RELATION": ["relation"],
        "REMOVE_RELATION": ["name"],
        "UPDATE_RELATION_TYPE": ["from", "to", "type"],
        "ADD_WORKSPACE": ["workspace"],
        "REMOVE_WORKSPACE": ["name"],
        "ADD_TO_WORKSPACE": ["workspace", "object"],
        "REMOVE_FROM_WORKSPACE": ["workspace", "object"],
        "ADD_ACTION": ["action"],
        "REMOVE_ACTION": ["name"],
        "RENAME_ACTION": ["old_name", "new_name"],
        "UPDATE_ACTION_TRIGGER": ["action", "trigger"],
        "UPDATE_ACTION_DESCRIPTION": ["action", "description"],
        "ADD_ENTITY_TO_ACTION": ["action", "object"],
        "REMOVE_ENTITY_FROM_ACTION": ["action", "object"],
    }

    for i, op_dict in enumerate(operations):
        if not isinstance(op_dict, dict):
            errors.append(f"[op #{i}] Não é dict: {op_dict!r}")
            continue

        op = op_dict.get("op")

        if not op:
            errors.append(f"[op #{i}] Chave 'op' em falta.")
            continue

        if op not in VALID_OPS:
            errors.append(f"[op #{i}] Operação desconhecida '{op}'.")
            continue

        for key in required.get(op, []):
            if key not in op_dict:
                errors.append(f"[op #{i}] '{op}' falta chave '{key}'.")

        if op == "ADD_OBJECT":
            p = op_dict.get("object") or {}
            if not isinstance(p, dict):
                errors.append(f"[op #{i}] ADD_OBJECT.object deve ser dict.")
            elif "objects" in p and "fields" not in p:
                errors.append(f"[op #{i}] ADD_OBJECT parece Workspace.")
            elif "from" in p or "to" in p:
                errors.append(f"[op #{i}] ADD_OBJECT parece Relation.")

        if op == "ADD_FIELD" and isinstance(op_dict.get("field"), list):
            errors.append(f"[op #{i}] ADD_FIELD.field é lista — usa ADD_FIELD separados.")

        if op == "ADD_RELATION":
            rel = op_dict.get("relation") or {}
            if not isinstance(rel, dict):
                errors.append(f"[op #{i}] ADD_RELATION.relation deve ser dict.")
            elif "from" not in rel or "to" not in rel:
                errors.append(f"[op #{i}] ADD_RELATION.relation precisa de 'from' e 'to'.")

        if op == "ADD_WORKSPACE":
            p = op_dict.get("workspace") or {}
            if not isinstance(p, dict):
                errors.append(f"[op #{i}] ADD_WORKSPACE.workspace deve ser dict.")
            elif "fields" in p:
                errors.append(f"[op #{i}] ADD_WORKSPACE parece Object.")

        if op == "ADD_ACTION":
            p = op_dict.get("action") or {}
            if not isinstance(p, dict):
                errors.append(f"[op #{i}] ADD_ACTION.action deve ser dict.")
            elif not p.get("name"):
                errors.append(f"[op #{i}] ADD_ACTION.action precisa de 'name'.")

    return len(errors) == 0, errors


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS GERAIS
# ─────────────────────────────────────────────────────────────────────────────

def _norm_token(value: str) -> str:
    value = str(value or "").strip().lower()
    replacements = {
        "ç": "c",
        "á": "a",
        "à": "a",
        "â": "a",
        "ã": "a",
        "é": "e",
        "ê": "e",
        "í": "i",
        "ó": "o",
        "ô": "o",
        "õ": "o",
        "ú": "u",
    }

    for old, new in replacements.items():
        value = value.replace(old, new)

    value = re.sub(r"[\s\-]+", "_", value)
    value = re.sub(r"[^a-z0-9_]", "", value)
    return value.strip("_")


def _norm_action_key(value: str) -> str:
    value = str(value or "").strip().lower()
    replacements = {
        "ç": "c",
        "á": "a",
        "à": "a",
        "â": "a",
        "ã": "a",
        "é": "e",
        "ê": "e",
        "í": "i",
        "ó": "o",
        "ô": "o",
        "õ": "o",
        "ú": "u",
    }

    for old, new in replacements.items():
        value = value.replace(old, new)

    return re.sub(r"[^a-z0-9]", "", value)


def _find_object(bp: BlueprintModel, name: str) -> Optional[ObjectModel]:
    target = _norm_token(name)
    return next((o for o in bp.objects if _norm_token(o.name) == target), None)


def _find_workspace(bp: BlueprintModel, name: str) -> Optional[WorkspaceModel]:
    target = _norm_token(name)
    return next((w for w in bp.workspaces if _norm_token(w.name) == target), None)


def _find_action(bp: BlueprintModel, name: str):
    target = _norm_action_key(name)
    return next((a for a in bp.actions if _norm_action_key(a.name) == target), None)


def _infer_field_type(field_name: str) -> str:
    fn = _norm_token(field_name)

    if any(k in fn for k in ["preco", "valor", "total", "custo", "saldo", "orcamento", "nota"]):
        return "float"

    if any(k in fn for k in ["stock", "quantidade", "numero", "ano", "idade", "prioridade"]):
        return "integer"

    if "data" in fn or "date" in fn:
        return "datetime"

    if any(k in fn for k in ["ativo", "validado", "aprovado", "pago", "publicado"]):
        return "boolean"

    if any(k in fn for k in ["descricao", "notas", "observacoes", "comentario"]):
        return "text"

    return "string"


def _normalize_type(value: str) -> str:
    raw = _norm_token(value)
    aliases = {
        "int": "integer",
        "inteiro": "integer",
        "integer": "integer",
        "float": "float",
        "decimal": "float",
        "number": "float",
        "numero": "float",
        "string": "string",
        "texto": "text",
        "text": "text",
        "boolean": "boolean",
        "bool": "boolean",
        "data": "datetime",
        "date": "date",
        "datetime": "datetime",
    }

    return aliases.get(raw, "string")


def _normalize_trigger(value: str) -> str:
    raw = _norm_token(value)
    aliases = {
        "manual": "manual",
        "manualmente": "manual",
        "automated": "automated",
        "automatico": "automated",
        "automático": "automated",
        "automatizado": "automated",
        "scheduled": "scheduled",
        "agendado": "scheduled",
        "periodico": "scheduled",
        "periódico": "scheduled",
    }

    return aliases.get(raw, "manual")


def _format_action_name(raw: str) -> str:
    raw = str(raw or "").strip().strip("'\"“”‘’")

    if not raw:
        return ""

    raw = re.sub(
        r"\s+(?:para|sobre|no|na)\s+(?:o\s+)?(?:objeto\s+)?[a-zA-ZÀ-ÿ0-9_]+$",
        "",
        raw,
        flags=re.IGNORECASE,
    ).strip()

    if re.search(r"[\s_\-]+", raw):
        parts = re.split(r"[\s_\-]+", raw)
        return "".join(p[:1].upper() + p[1:] for p in parts if p)

    return raw


def _find_object_for_field(
    bp: BlueprintModel,
    field_name: str,
    explicit_object: Optional[str] = None,
) -> Optional[str]:
    if explicit_object:
        obj = _find_object(bp, explicit_object)
        if obj:
            return obj.name

    if len(bp.objects) == 1:
        return bp.objects[0].name

    target = _norm_token(field_name)

    for obj in bp.objects:
        for field in obj.fields:
            if _norm_token(field.name) == target:
                return obj.name

    return None


def _parse_field_list(raw: str) -> List[Dict[str, str]]:
    raw = str(raw or "")
    raw = raw.replace(" e ", ",")
    raw = raw.replace(";", ",")

    parts = [p.strip() for p in raw.split(",") if p.strip()]
    fields: List[Dict[str, str]] = []

    for part in parts:
        field_name = part
        field_type = None

        if ":" in part:
            left, right = part.split(":", 1)
            field_name = left.strip()
            field_type = _normalize_type(right.strip())
        else:
            match = re.match(
                r"(.+?)\s+(string|integer|float|boolean|date|datetime|text|inteiro|texto|decimal)$",
                part,
                re.IGNORECASE,
            )
            if match:
                field_name = match.group(1).strip()
                field_type = _normalize_type(match.group(2).strip())

        fname = _norm_token(field_name)

        if not fname:
            continue

        fields.append({
            "name": fname,
            "type": field_type or _infer_field_type(fname),
        })

    return fields


def _remove_object_from_all_workspaces(bp: BlueprintModel, name: str) -> None:
    nl = name.lower()

    for ws in bp.workspaces:
        ws.objects = [o for o in ws.objects if str(o).lower() != nl]

        if str(ws.primary_entity).lower() == nl:
            ws.primary_entity = ws.objects[0] if ws.objects else ""


def _rename_object_in_workspaces(bp: BlueprintModel, old: str, new: str) -> None:
    ol = old.lower()

    for ws in bp.workspaces:
        ws.objects = [new if str(o).lower() == ol else o for o in ws.objects]

        if str(ws.primary_entity).lower() == ol:
            ws.primary_entity = new


def _remove_object_from_relations(bp: BlueprintModel, name: str) -> None:
    nl = name.lower()
    bp.relations = [
        r for r in bp.relations
        if str(r.from_obj).lower() != nl and str(r.to_obj).lower() != nl
    ]


def _rename_object_in_relations(bp: BlueprintModel, old: str, new: str) -> None:
    ol = old.lower()

    for rel in bp.relations:
        if str(rel.from_obj).lower() == ol:
            rel.from_obj = new

        if str(rel.to_obj).lower() == ol:
            rel.to_obj = new


def _remove_object_from_actions(bp: BlueprintModel, name: str) -> None:
    nl = name.lower()

    for action in bp.actions:
        action.entities_involved = [
            entity for entity in action.entities_involved
            if str(entity).lower() != nl
        ]


def _rename_object_in_actions(bp: BlueprintModel, old: str, new: str) -> None:
    ol = old.lower()

    for action in bp.actions:
        action.entities_involved = [
            new if str(entity).lower() == ol else entity
            for entity in action.entities_involved
        ]


# ─────────────────────────────────────────────────────────────────────────────
# OPERAÇÕES DETERMINÍSTICAS
# ─────────────────────────────────────────────────────────────────────────────

def _rule_based_operations(user_prompt: str, bp: BlueprintModel) -> List[Dict[str, Any]]:
    text = str(user_prompt or "").strip()
    low = text.lower()

    operations: List[Dict[str, Any]] = []

    def _add_op(op: Dict[str, Any]) -> None:
        key = json.dumps(op, ensure_ascii=False, sort_keys=True)
        existing = {
            json.dumps(x, ensure_ascii=False, sort_keys=True)
            for x in operations
        }

        if key not in existing:
            operations.append(op)

    def _resolve_object_name(raw_name: Optional[str]) -> Optional[str]:
        if not raw_name:
            return None

        obj = _find_object(bp, raw_name)
        return obj.name if obj else None

    def _resolve_workspace_name(raw_name: Optional[str]) -> Optional[str]:
        if not raw_name:
            return None

        ws = _find_workspace(bp, raw_name)
        return ws.name if ws else None

    def _resolve_action_name(raw_name: Optional[str]) -> Optional[str]:
        if not raw_name:
            return None

        raw = str(raw_name).strip().strip("'\"“”‘’")
        raw = re.sub(
            r"\s+(?:para|sobre|no|na)\s+(?:o\s+)?(?:objeto\s+)?[a-zA-ZÀ-ÿ0-9_]+$",
            "",
            raw,
            flags=re.IGNORECASE,
        ).strip()

        formatted = _format_action_name(raw)

        for action in bp.actions:
            if (
                _norm_action_key(action.name) == _norm_action_key(raw)
                or _norm_action_key(action.name) == _norm_action_key(formatted)
            ):
                return action.name

        return formatted if formatted else None

    def _parse_object_names(raw: str) -> List[str]:
        raw = str(raw or "").strip()
        raw = raw.replace(" e ", ",")
        raw = raw.replace(";", ",")

        resolved = []

        for part in [p.strip() for p in raw.split(",") if p.strip()]:
            part = re.split(
                r"\s+(?:com|como|do|da|de|no|na|ao|à)\s+",
                part,
                maxsplit=1,
                flags=re.IGNORECASE,
            )[0].strip()

            obj_name = _resolve_object_name(part)

            if obj_name:
                resolved.append(obj_name)

        return resolved

    # 1. RENAME_FIELD
    rename_field_patterns = [
        r"(?:renomeia|renomear)\s+(?:o\s+)?campo\s+([a-zA-ZÀ-ÿ0-9_]+)\s+(?:para|por)\s+([a-zA-ZÀ-ÿ0-9_]+)",
        r"(?:troca|trocar|substitui|substituir)\s+(?:o\s+)?campo\s+([a-zA-ZÀ-ÿ0-9_]+)\s+(?:para|por)\s+([a-zA-ZÀ-ÿ0-9_]+)",
    ]

    for pattern in rename_field_patterns:
        for match in re.finditer(pattern, low, re.IGNORECASE):
            old_name = _norm_token(match.group(1))
            new_name = _norm_token(match.group(2))
            obj_name = _find_object_for_field(bp, old_name)

            if obj_name:
                _add_op({
                    "op": "RENAME_FIELD",
                    "object": obj_name,
                    "old_name": old_name,
                    "new_name": new_name,
                })

    # 2. REMOVE_FIELD
    remove_field_pattern = (
        r"(?:remove|remover|apaga|apagar|elimina|eliminar|retira|tirar)\s+"
        r"(?:o\s+)?campo\s+([a-zA-ZÀ-ÿ0-9_]+)"
        r"(?:\s+(?:do|da|de)\s+(?:objeto\s+)?([a-zA-ZÀ-ÿ0-9_]+))?"
    )

    for match in re.finditer(remove_field_pattern, low, re.IGNORECASE):
        field_name = _norm_token(match.group(1))
        explicit_obj = match.group(2)
        obj_name = _find_object_for_field(bp, field_name, explicit_obj)

        if obj_name:
            _add_op({
                "op": "REMOVE_FIELD",
                "object": obj_name,
                "field_name": field_name,
            })

    # 3. RETYPE_FIELD
    retype_field_pattern = (
        r"(?:muda|mudar|altera|alterar|atualiza|atualizar)\s+"
        r"(?:o\s+)?tipo\s+(?:do\s+)?campo\s+([a-zA-ZÀ-ÿ0-9_]+)\s+"
        r"(?:para|como)\s+([a-zA-ZÀ-ÿ0-9_]+)"
    )

    for match in re.finditer(retype_field_pattern, low, re.IGNORECASE):
        field_name = _norm_token(match.group(1))
        new_type = _normalize_type(match.group(2))
        obj_name = _find_object_for_field(bp, field_name)

        if obj_name:
            _add_op({
                "op": "RETYPE_FIELD",
                "object": obj_name,
                "field_name": field_name,
                "new_type": new_type,
            })

    # 4. ADD_FIELD
    add_field_pattern = (
        r"(?:adiciona|adicionar|acrescenta|acrescentar|cria|criar)\s+"
        r"(?:o\s+)?campo\s+([a-zA-ZÀ-ÿ0-9_]+)"
        r"(?:\s+(?:ao|à|no|na)\s+(?:objeto\s+)?([a-zA-ZÀ-ÿ0-9_]+))?"
        r"(?:\s+como\s+([a-zA-ZÀ-ÿ0-9_]+))?"
    )

    for match in re.finditer(add_field_pattern, low, re.IGNORECASE):
        field_name = _norm_token(match.group(1))
        explicit_obj = match.group(2)
        field_type = _normalize_type(match.group(3)) if match.group(3) else _infer_field_type(field_name)

        obj_name = _resolve_object_name(explicit_obj) if explicit_obj else None

        if not obj_name and len(bp.objects) == 1:
            obj_name = bp.objects[0].name

        if obj_name:
            _add_op({
                "op": "ADD_FIELD",
                "object": obj_name,
                "field": {
                    "name": field_name,
                    "type": field_type,
                },
            })

    # 5. ADD_OBJECT
    add_object_pattern = (
        r"(?:adiciona|adicionar|cria|criar)\s+"
        r"(?:um\s+|uma\s+)?objeto\s+([a-zA-ZÀ-ÿ0-9_]+)\s+"
        r"com\s+(?:os\s+)?campos\s+(.+?)(?=$|\s+e\s+(?:adiciona|remove|renomeia|muda|cria|apaga|elimina|retira)\b)"
    )

    for match in re.finditer(add_object_pattern, text, re.IGNORECASE):
        object_name = str(match.group(1)).strip()
        fields = _parse_field_list(match.group(2).strip())

        _add_op({
            "op": "ADD_OBJECT",
            "object": {
                "name": object_name,
                "fields": fields,
            },
        })

    # 6. RENAME_OBJECT
    rename_object_patterns = [
        r"(?:renomeia|renomear)\s+(?:o\s+)?objeto\s+([a-zA-ZÀ-ÿ0-9_]+)\s+(?:para|por)\s+([a-zA-ZÀ-ÿ0-9_]+)",
        r"(?:troca|trocar|substitui|substituir)\s+(?:o\s+)?objeto\s+([a-zA-ZÀ-ÿ0-9_]+)\s+(?:para|por)\s+([a-zA-ZÀ-ÿ0-9_]+)",
    ]

    for pattern in rename_object_patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            old_name = _resolve_object_name(match.group(1))
            new_name = str(match.group(2)).strip()

            if old_name and new_name:
                _add_op({
                    "op": "RENAME_OBJECT",
                    "old_name": old_name,
                    "new_name": new_name,
                })

    # 7. REMOVE_OBJECT
    remove_object_pattern = (
        r"(?:remove|remover|apaga|apagar|elimina|eliminar)\s+"
        r"(?:o\s+)?objeto\s+([a-zA-ZÀ-ÿ0-9_]+)"
        r"(?!\s+(?:do|da|de|no|na|ao|à)\s+(?:workspace|ação|acao|action))"
    )

    for match in re.finditer(remove_object_pattern, low, re.IGNORECASE):
        obj_name = _resolve_object_name(match.group(1))

        if obj_name:
            _add_op({
                "op": "REMOVE_OBJECT",
                "name": obj_name,
            })

    # 8. ADD_RELATION
    add_relation_patterns = [
        (
            r"(?:cria|criar|adiciona|adicionar)\s+"
            r"(?:uma\s+)?(?:relação|relacao)\s+entre\s+"
            r"([a-zA-ZÀ-ÿ0-9_]+)\s+e\s+([a-zA-ZÀ-ÿ0-9_]+)"
            r"(?:\s+(?:como|do\s+tipo|tipo)\s+(ONE_TO_MANY|MANY_TO_MANY))?"
        ),
        (
            r"(?:liga|ligar)\s+"
            r"([a-zA-ZÀ-ÿ0-9_]+)\s+(?:a|ao|à|com)\s+([a-zA-ZÀ-ÿ0-9_]+)"
            r"(?:\s+(?:como|do\s+tipo|tipo)\s+(ONE_TO_MANY|MANY_TO_MANY))?"
        ),
    ]

    for pattern in add_relation_patterns:
        for match in re.finditer(pattern, low, re.IGNORECASE):
            from_obj = _resolve_object_name(match.group(1))
            to_obj = _resolve_object_name(match.group(2))
            rel_type = (match.group(3) or "ONE_TO_MANY").upper()

            if rel_type not in VALID_RELATION_TYPES:
                rel_type = "ONE_TO_MANY"

            if from_obj and to_obj and from_obj != to_obj:
                _add_op({
                    "op": "ADD_RELATION",
                    "relation": {
                        "from": from_obj,
                        "to": to_obj,
                        "type": rel_type,
                    },
                })

    # 9. REMOVE_RELATION
    remove_relation_pattern = (
        r"(?:remove|remover|apaga|apagar|elimina|eliminar)\s+"
        r"(?:a\s+)?(?:relação|relacao)\s+entre\s+"
        r"([a-zA-ZÀ-ÿ0-9_]+)\s+e\s+([a-zA-ZÀ-ÿ0-9_]+)"
    )

    for match in re.finditer(remove_relation_pattern, low, re.IGNORECASE):
        from_obj = _resolve_object_name(match.group(1))
        to_obj = _resolve_object_name(match.group(2))

        if from_obj and to_obj:
            _add_op({
                "op": "REMOVE_RELATION",
                "name": f"{from_obj}→{to_obj}",
            })

    # 10. ADD_WORKSPACE
    add_workspace_pattern = (
        r"(?:cria|criar|adiciona|adicionar)\s+"
        r"(?:um\s+|uma\s+)?workspace\s+([a-zA-ZÀ-ÿ0-9_]+)"
        r"(?:\s+com\s+(?:os\s+)?objetos\s+(.+?))?"
        r"(?=$|\s+e\s+(?:adiciona|remove|renomeia|muda|cria|apaga|elimina|retira)\b)"
    )

    for match in re.finditer(add_workspace_pattern, text, re.IGNORECASE):
        workspace_name = str(match.group(1)).strip()
        objects = _parse_object_names(match.group(2) or "")

        _add_op({
            "op": "ADD_WORKSPACE",
            "workspace": {
                "name": workspace_name,
                "objects": objects,
                "permissions": ["VER", "CRIAR", "EDITAR", "APAGAR"],
            },
        })

    # 11. REMOVE_WORKSPACE
    remove_workspace_pattern = (
        r"(?:remove|remover|apaga|apagar|elimina|eliminar)\s+"
        r"(?:o\s+)?workspace\s+([a-zA-ZÀ-ÿ0-9_]+)"
    )

    for match in re.finditer(remove_workspace_pattern, low, re.IGNORECASE):
        ws_name = _resolve_workspace_name(match.group(1))

        if ws_name:
            _add_op({
                "op": "REMOVE_WORKSPACE",
                "name": ws_name,
            })

    # 12. ADD_TO_WORKSPACE
    add_to_workspace_pattern = (
        r"(?:adiciona|adicionar|mete|coloca)\s+"
        r"(?:o\s+)?objeto\s+([a-zA-ZÀ-ÿ0-9_]+)\s+"
        r"(?:ao|à|no|na)\s+workspace\s+([a-zA-ZÀ-ÿ0-9_]+)"
    )

    for match in re.finditer(add_to_workspace_pattern, low, re.IGNORECASE):
        obj_name = _resolve_object_name(match.group(1))
        ws_name = _resolve_workspace_name(match.group(2))

        if obj_name and ws_name:
            _add_op({
                "op": "ADD_TO_WORKSPACE",
                "workspace": ws_name,
                "object": obj_name,
            })

    # 13. REMOVE_FROM_WORKSPACE
    remove_from_workspace_pattern = (
        r"(?:remove|remover|retira|tirar)\s+"
        r"(?:o\s+)?objeto\s+([a-zA-ZÀ-ÿ0-9_]+)\s+"
        r"(?:do|da|de|no|na)\s+workspace\s+([a-zA-ZÀ-ÿ0-9_]+)"
    )

    for match in re.finditer(remove_from_workspace_pattern, low, re.IGNORECASE):
        obj_name = _resolve_object_name(match.group(1))
        ws_name = _resolve_workspace_name(match.group(2))

        if obj_name and ws_name:
            _add_op({
                "op": "REMOVE_FROM_WORKSPACE",
                "workspace": ws_name,
                "object": obj_name,
            })

    # 14. ADD_ACTION
    add_action_specific_pattern = (
        r"(?:cria|criar|adiciona|adicionar)\s+"
        r"(?:uma\s+)?(?:ação|acao|action)\s+(.+?)\s+"
        r"(?:para|sobre|no|na)\s+(?:o\s+)?(?:objeto\s+)?([a-zA-ZÀ-ÿ0-9_]+)"
        r"(?=$|\s+e\s+(?:adiciona|remove|renomeia|muda|altera|cria|apaga|elimina|retira)\b)"
    )

    specific_matches = list(re.finditer(add_action_specific_pattern, text, re.IGNORECASE))

    for match in specific_matches:
        action_name = _format_action_name(match.group(1))
        entity = _resolve_object_name(match.group(2))

        if not action_name:
            continue

        _add_op({
            "op": "ADD_ACTION",
            "action": {
                "name": action_name,
                "type": "DOMAIN_ACTION",
                "description": f"{action_name} no sistema",
                "trigger": "manual",
                "entities_involved": [entity] if entity else [],
                "steps": [
                    "validar dados necessários",
                    "executar ação",
                    "confirmar resultado",
                ],
                "preconditions": [
                    f"{entity} deve existir" if entity else "condições necessárias devem estar reunidas",
                ],
                "postconditions": [
                    "ação concluída",
                ],
            },
        })

    if not specific_matches:
        add_action_general_pattern = (
            r"(?:cria|criar|adiciona|adicionar)\s+"
            r"(?:uma\s+)?(?:ação|acao|action)\s+(.+?)"
            r"(?=$|\s+e\s+(?:adiciona|remove|renomeia|muda|altera|cria|apaga|elimina|retira)\b)"
        )

        for match in re.finditer(add_action_general_pattern, text, re.IGNORECASE):
            action_name = _format_action_name(match.group(1))

            if not action_name:
                continue

            _add_op({
                "op": "ADD_ACTION",
                "action": {
                    "name": action_name,
                    "type": "DOMAIN_ACTION",
                    "description": f"{action_name} no sistema",
                    "trigger": "manual",
                    "entities_involved": [],
                    "steps": [
                        "validar dados necessários",
                        "executar ação",
                        "confirmar resultado",
                    ],
                    "preconditions": [
                        "condições necessárias devem estar reunidas",
                    ],
                    "postconditions": [
                        "ação concluída",
                    ],
                },
            })

    # 15. REMOVE_ACTION
    remove_action_pattern = (
        r"(?:remove|remover|apaga|apagar|elimina|eliminar)\s+"
        r"(?:a\s+)?(?:ação|acao|action)\s+(.+?)"
        r"(?=$|\s+e\s+(?:adiciona|remove|renomeia|muda|altera|cria|apaga|elimina|retira)\b)"
    )

    for match in re.finditer(remove_action_pattern, text, re.IGNORECASE):
        action_name = _resolve_action_name(match.group(1))

        if action_name:
            _add_op({
                "op": "REMOVE_ACTION",
                "name": action_name,
            })

    # 16. RENAME_ACTION
    rename_action_patterns = [
        (
            r"(?:renomeia|renomear)\s+"
            r"(?:a\s+)?(?:ação|acao|action)\s+(.+?)\s+"
            r"(?:para|por)\s+(.+?)"
            r"(?=$|\s+e\s+(?:adiciona|remove|renomeia|muda|altera|cria|apaga|elimina|retira)\b)"
        ),
        (
            r"(?:troca|trocar|substitui|substituir)\s+"
            r"(?:a\s+)?(?:ação|acao|action)\s+(.+?)\s+"
            r"(?:para|por)\s+(.+?)"
            r"(?=$|\s+e\s+(?:adiciona|remove|renomeia|muda|altera|cria|apaga|elimina|retira)\b)"
        ),
    ]

    for pattern in rename_action_patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            old_name = _resolve_action_name(match.group(1))
            new_name = _format_action_name(match.group(2))

            if old_name and new_name:
                _add_op({
                    "op": "RENAME_ACTION",
                    "old_name": old_name,
                    "new_name": new_name,
                })

    # 17. UPDATE_ACTION_TRIGGER
    update_action_trigger_pattern = (
        r"(?:muda|mudar|altera|alterar|atualiza|atualizar)\s+"
        r"(?:o\s+)?trigger\s+(?:da|de)\s+"
        r"(?:ação|acao|action)\s+(.+?)\s+"
        r"(?:para|como)\s+([a-zA-ZÀ-ÿ0-9_]+)"
        r"(?=$|\s+e\s+(?:adiciona|remove|renomeia|muda|altera|cria|apaga|elimina|retira)\b)"
    )

    for match in re.finditer(update_action_trigger_pattern, text, re.IGNORECASE):
        action_name = _resolve_action_name(match.group(1))
        trigger = _normalize_trigger(match.group(2))

        if action_name:
            _add_op({
                "op": "UPDATE_ACTION_TRIGGER",
                "action": action_name,
                "trigger": trigger,
            })

    # 18. UPDATE_ACTION_DESCRIPTION
    update_action_description_pattern = (
        r"(?:muda|mudar|altera|alterar|atualiza|atualizar)\s+"
        r"(?:a\s+)?(?:descrição|descricao)\s+(?:da|de)\s+"
        r"(?:ação|acao|action)\s+(.+?)\s+"
        r"(?:para|como)\s+(.+?)"
        r"(?=$|\s+e\s+(?:adiciona|remove|renomeia|muda|altera|cria|apaga|elimina|retira)\b)"
    )

    for match in re.finditer(update_action_description_pattern, text, re.IGNORECASE):
        action_name = _resolve_action_name(match.group(1))
        description = str(match.group(2)).strip().strip("'\"“”‘’")

        if action_name and description:
            _add_op({
                "op": "UPDATE_ACTION_DESCRIPTION",
                "action": action_name,
                "description": description,
            })

    # 19. ADD_ENTITY_TO_ACTION
    add_entity_to_action_pattern = (
        r"(?:adiciona|adicionar|mete|coloca)\s+"
        r"(?:o\s+)?objeto\s+([a-zA-ZÀ-ÿ0-9_]+)\s+"
        r"(?:à|a|na|no)\s+(?:ação|acao|action)\s+(.+?)"
        r"(?=$|\s+e\s+(?:adiciona|remove|renomeia|muda|altera|cria|apaga|elimina|retira)\b)"
    )

    for match in re.finditer(add_entity_to_action_pattern, text, re.IGNORECASE):
        obj_name = _resolve_object_name(match.group(1))
        action_name = _resolve_action_name(match.group(2))

        if obj_name and action_name:
            _add_op({
                "op": "ADD_ENTITY_TO_ACTION",
                "action": action_name,
                "object": obj_name,
            })

    # 20. REMOVE_ENTITY_FROM_ACTION
    remove_entity_from_action_pattern = (
        r"(?:remove|remover|retira|tirar)\s+"
        r"(?:o\s+)?objeto\s+([a-zA-ZÀ-ÿ0-9_]+)\s+"
        r"(?:da|de|na|no)\s+(?:ação|acao|action)\s+(.+?)"
        r"(?=$|\s+e\s+(?:adiciona|remove|renomeia|muda|altera|cria|apaga|elimina|retira)\b)"
    )

    for match in re.finditer(remove_entity_from_action_pattern, text, re.IGNORECASE):
        obj_name = _resolve_object_name(match.group(1))
        action_name = _resolve_action_name(match.group(2))

        if obj_name and action_name:
            _add_op({
                "op": "REMOVE_ENTITY_FROM_ACTION",
                "action": action_name,
                "object": obj_name,
            })
    
    # ─────────────────────────────────────────────────────────────────────
    # UPDATE_RELATION_TYPE
    # ─────────────────────────────────────────────────────────────────────

    update_relation_type_pattern = (
        r"(?:muda|mudar|altera|alterar|atualiza|atualizar)\s+"
        r"(?:o\s+)?tipo\s+(?:da\s+)?(?:relação|relacao)\s+entre\s+"
        r"([a-zA-ZÀ-ÿ0-9_]+)\s+e\s+([a-zA-ZÀ-ÿ0-9_]+)\s+"
        r"(?:para|como)\s+(ONE_TO_MANY|MANY_TO_MANY)"
    )

    for match in re.finditer(update_relation_type_pattern, text, re.IGNORECASE):
        from_obj = _resolve_object_name(match.group(1))
        to_obj = _resolve_object_name(match.group(2))
        rel_type = match.group(3).upper()

        if from_obj and to_obj and from_obj != to_obj:
            _add_op({
                "op": "UPDATE_RELATION_TYPE",
                "from": from_obj,
                "to": to_obj,
                "type": rel_type
            })
    return operations


# ─────────────────────────────────────────────────────────────────────────────
# APLICAÇÃO DE OPERAÇÕES
# ─────────────────────────────────────────────────────────────────────────────

def _apply_operation(bp: BlueprintModel, op_dict: Dict[str, Any]) -> List[str]:
    op = op_dict["op"]
    w: List[str] = []

    if op == "ADD_OBJECT":
        p = op_dict.get("object") or {}

        try:
            obj = ObjectModel.model_validate(p) if p else ObjectModel(name="unnamed")
        except Exception as e:
            w.append(f"ADD_OBJECT: payload inválido — {e}")
            return w

        if _find_object(bp, obj.name):
            w.append(f"ADD_OBJECT: '{obj.name}' já existe.")
        else:
            bp.objects.append(obj)

    elif op == "REMOVE_OBJECT":
        name = op_dict["name"]
        nl = name.lower()
        n0 = len(bp.objects)

        bp.objects = [o for o in bp.objects if o.name.lower() != nl]

        if len(bp.objects) == n0:
            w.append(f"REMOVE_OBJECT: '{name}' não encontrado.")
        else:
            _remove_object_from_all_workspaces(bp, name)
            _remove_object_from_relations(bp, name)
            _remove_object_from_actions(bp, name)

    elif op == "RENAME_OBJECT":
        old = op_dict["old_name"]
        new = op_dict["new_name"]

        obj = _find_object(bp, old)

        if obj is None:
            w.append(f"RENAME_OBJECT: '{old}' não encontrado.")
        elif _find_object(bp, new):
            w.append(f"RENAME_OBJECT: '{new}' já existe.")
        else:
            obj.name = new
            _rename_object_in_workspaces(bp, old, new)
            _rename_object_in_relations(bp, old, new)
            _rename_object_in_actions(bp, old, new)

    elif op == "ADD_FIELD":
        obj = _find_object(bp, op_dict["object"])

        if obj is None:
            w.append(f"ADD_FIELD: objeto '{op_dict['object']}' não encontrado.")
            return w

        fp = op_dict["field"]

        try:
            f = FieldModel.model_validate(fp) if isinstance(fp, dict) else FieldModel(name=str(fp))
        except Exception as e:
            w.append(f"ADD_FIELD: field inválido — {e}")
            return w

        if any(x.name.lower() == f.name.lower() for x in obj.fields):
            w.append(f"ADD_FIELD: '{f.name}' já existe em '{obj.name}'.")
        else:
            obj.fields.append(f)

    elif op == "REMOVE_FIELD":
        obj = _find_object(bp, op_dict["object"])

        if obj is None:
            w.append(f"REMOVE_FIELD: objeto '{op_dict['object']}' não encontrado.")
            return w

        fn = op_dict["field_name"].lower()
        n0 = len(obj.fields)

        obj.fields = [f for f in obj.fields if f.name.lower() != fn]

        if len(obj.fields) == n0:
            w.append(f"REMOVE_FIELD: campo '{op_dict['field_name']}' não encontrado.")

    elif op == "RENAME_FIELD":
        obj = _find_object(bp, op_dict["object"])

        if obj is None:
            w.append(f"RENAME_FIELD: objeto '{op_dict['object']}' não encontrado.")
            return w

        tgt = next(
            (f for f in obj.fields if f.name.lower() == op_dict["old_name"].lower()),
            None,
        )

        if tgt is None:
            w.append(f"RENAME_FIELD: campo '{op_dict['old_name']}' não encontrado.")
        elif any(f.name.lower() == op_dict["new_name"].lower() and f is not tgt for f in obj.fields):
            w.append(f"RENAME_FIELD: '{op_dict['new_name']}' já existe.")
        else:
            tgt.name = op_dict["new_name"]

    elif op == "RETYPE_FIELD":
        obj = _find_object(bp, op_dict["object"])

        if obj is None:
            w.append(f"RETYPE_FIELD: objeto '{op_dict['object']}' não encontrado.")
            return w

        tgt = next(
            (f for f in obj.fields if f.name.lower() == op_dict["field_name"].lower()),
            None,
        )

        if tgt is None:
            w.append(f"RETYPE_FIELD: campo '{op_dict['field_name']}' não encontrado.")
        else:
            new_type = str(op_dict["new_type"]).lower()

            if new_type not in VALID_FIELD_TYPES:
                new_type = "string"

            tgt.type = new_type

    
    elif op == "ADD_RELATION":
        rp = op_dict.get("relation") or {}

        if not isinstance(rp, dict):
            w.append("ADD_RELATION: payload deve ser dict.")
            return w

        try:
            rel = RelationModel.model_validate(rp)
        except Exception as e:
            w.append(f"ADD_RELATION: payload inválido — {e}")
            return w

        if not _find_object(bp, rel.from_obj):
            w.append(f"ADD_RELATION: '{rel.from_obj}' não encontrado.")

        elif not _find_object(bp, rel.to_obj):
            w.append(f"ADD_RELATION: '{rel.to_obj}' não encontrado.")

        else:
            existing = next(
                (
                    r for r in bp.relations
                    if r.from_obj.lower() == rel.from_obj.lower()
                    and r.to_obj.lower() == rel.to_obj.lower()
                ),
                None
            )

            if existing:
                if existing.type != rel.type:
                    existing.type = rel.type
                    w.append(
                        f"ADD_RELATION: '{rel.from_obj}→{rel.to_obj}' já existia; tipo atualizado para {rel.type}."
                    )
                else:
                    w.append(f"ADD_RELATION: '{rel.from_obj}→{rel.to_obj}' já existe.")
            else:
                bp.relations.append(rel)

    elif op == "UPDATE_RELATION_TYPE":
        from_name = op_dict["from"]
        to_name = op_dict["to"]
        new_type = str(op_dict["type"]).upper()

        if new_type not in {"ONE_TO_MANY", "MANY_TO_MANY"}:
            w.append(f"UPDATE_RELATION_TYPE: tipo inválido '{new_type}'.")
            return w

        rel = next(
            (
                r for r in bp.relations
                if r.from_obj.lower() == from_name.lower()
                and r.to_obj.lower() == to_name.lower()
            ),
            None
        )

        if rel is None:
            w.append(f"UPDATE_RELATION_TYPE: relação '{from_name}→{to_name}' não encontrada.")
        else:
            rel.type = new_type
            
    elif op == "REMOVE_RELATION":
        name = op_dict["name"]
        n0 = len(bp.relations)
        parts = re.split(r"[→_\-]", name, maxsplit=1)

        if len(parts) == 2:
            fl = parts[0].strip().lower()
            tl = parts[1].strip().lower()
            bp.relations = [
                r for r in bp.relations
                if not (r.from_obj.lower() == fl and r.to_obj.lower() == tl)
            ]
        else:
            bp.relations = [
                r for r in bp.relations
                if r.from_obj.lower() != name.lower()
            ]

        if len(bp.relations) == n0:
            w.append(f"REMOVE_RELATION: '{name}' não encontrada.")

    elif op == "ADD_WORKSPACE":
        p = op_dict.get("workspace") or {}

        try:
            ws = WorkspaceModel.model_validate(p) if p else WorkspaceModel(name="unnamed_ws")
        except Exception as e:
            w.append(f"ADD_WORKSPACE: payload inválido — {e}")
            return w

        if _find_workspace(bp, ws.name):
            w.append(f"ADD_WORKSPACE: '{ws.name}' já existe.")
        else:
            valid = {o.name.lower(): o.name for o in bp.objects}
            normalized = []

            for obj in ws.objects:
                canonical = valid.get(str(obj).lower())
                if canonical and canonical not in normalized:
                    normalized.append(canonical)

            ws.objects = normalized

            if ws.objects and not ws.primary_entity:
                ws.primary_entity = ws.objects[0]

            if ws.primary_entity and not any(str(o).lower() == str(ws.primary_entity).lower() for o in ws.objects):
                ws.primary_entity = ws.objects[0] if ws.objects else ""

            bp.workspaces.append(ws)

    elif op == "REMOVE_WORKSPACE":
        name = op_dict["name"]
        nl = name.lower()
        n0 = len(bp.workspaces)

        bp.workspaces = [ws for ws in bp.workspaces if ws.name.lower() != nl]

        if len(bp.workspaces) == n0:
            w.append(f"REMOVE_WORKSPACE: '{name}' não encontrado.")

    elif op == "ADD_TO_WORKSPACE":
        ws = _find_workspace(bp, op_dict["workspace"])
        obj = _find_object(bp, op_dict["object"])

        if ws is None:
            w.append(f"ADD_TO_WORKSPACE: workspace '{op_dict['workspace']}' não encontrado.")
        elif obj is None:
            w.append(f"ADD_TO_WORKSPACE: objeto '{op_dict['object']}' não encontrado.")
        elif not any(str(o).lower() == obj.name.lower() for o in ws.objects):
            ws.objects.append(obj.name)

            if not ws.primary_entity:
                ws.primary_entity = obj.name

    elif op == "REMOVE_FROM_WORKSPACE":
        ws = _find_workspace(bp, op_dict["workspace"])

        if ws is None:
            w.append(f"REMOVE_FROM_WORKSPACE: workspace '{op_dict['workspace']}' não encontrado.")
        else:
            ol = op_dict["object"].lower()
            n0 = len(ws.objects)

            ws.objects = [o for o in ws.objects if str(o).lower() != ol]

            if str(ws.primary_entity).lower() == ol:
                ws.primary_entity = ws.objects[0] if ws.objects else ""

            if len(ws.objects) == n0:
                w.append(
                    f"REMOVE_FROM_WORKSPACE: '{op_dict['object']}' não encontrado "
                    f"em '{op_dict['workspace']}'."
                )

    elif op == "ADD_ACTION":
        p = op_dict.get("action") or {}

        if not isinstance(p, dict):
            w.append("ADD_ACTION: payload deve ser dict.")
            return w

        try:
            action = ActionModel.model_validate(p)
        except Exception as e:
            w.append(f"ADD_ACTION: payload inválido — {e}")
            return w

        if _find_action(bp, action.name):
            w.append(f"ADD_ACTION: '{action.name}' já existe.")
        else:
            valid_objects = {o.name.lower(): o.name for o in bp.objects}
            normalized_entities = []

            for entity in action.entities_involved:
                canonical = valid_objects.get(str(entity).lower())

                if canonical and canonical not in normalized_entities:
                    normalized_entities.append(canonical)

            action.entities_involved = normalized_entities
            bp.actions.append(action)

    elif op == "REMOVE_ACTION":
        name = op_dict["name"]
        target = _norm_action_key(name)
        n0 = len(bp.actions)

        bp.actions = [
            action for action in bp.actions
            if _norm_action_key(action.name) != target
        ]

        if len(bp.actions) == n0:
            w.append(f"REMOVE_ACTION: '{name}' não encontrada.")

    elif op == "RENAME_ACTION":
        old = op_dict["old_name"]
        new = op_dict["new_name"]

        action = _find_action(bp, old)

        if action is None:
            w.append(f"RENAME_ACTION: '{old}' não encontrada.")
        elif _find_action(bp, new):
            w.append(f"RENAME_ACTION: '{new}' já existe.")
        else:
            action.name = new

    elif op == "UPDATE_ACTION_TRIGGER":
        action_name = op_dict["action"]
        action = _find_action(bp, action_name)

        if action is None:
            w.append(f"UPDATE_ACTION_TRIGGER: '{action_name}' não encontrada.")
        else:
            action.trigger = _normalize_trigger(op_dict["trigger"])

    elif op == "UPDATE_ACTION_DESCRIPTION":
        action_name = op_dict["action"]
        action = _find_action(bp, action_name)

        if action is None:
            w.append(f"UPDATE_ACTION_DESCRIPTION: '{action_name}' não encontrada.")
        else:
            action.description = str(op_dict["description"]).strip()

    elif op == "ADD_ENTITY_TO_ACTION":
        action_name = op_dict["action"]
        object_name = op_dict["object"]
        action = _find_action(bp, action_name)
        obj = _find_object(bp, object_name)

        if action is None:
            w.append(f"ADD_ENTITY_TO_ACTION: ação '{action_name}' não encontrada.")
        elif obj is None:
            w.append(f"ADD_ENTITY_TO_ACTION: objeto '{object_name}' não encontrado.")
        elif not any(str(entity).lower() == obj.name.lower() for entity in action.entities_involved):
            action.entities_involved.append(obj.name)

    elif op == "REMOVE_ENTITY_FROM_ACTION":
        action_name = op_dict["action"]
        object_name = op_dict["object"]
        action = _find_action(bp, action_name)

        if action is None:
            w.append(f"REMOVE_ENTITY_FROM_ACTION: ação '{action_name}' não encontrada.")
        else:
            ol = str(object_name).lower()
            n0 = len(action.entities_involved)

            action.entities_involved = [
                entity for entity in action.entities_involved
                if str(entity).lower() != ol
            ]

            if len(action.entities_involved) == n0:
                w.append(
                    f"REMOVE_ENTITY_FROM_ACTION: objeto '{object_name}' não encontrado "
                    f"na ação '{action_name}'."
                )

    return w


# ─────────────────────────────────────────────────────────────────────────────
# FEEDBACK LOOP
# ─────────────────────────────────────────────────────────────────────────────

MAX_FEEDBACK_ITERATIONS = 2


def _run_with_feedback_loop(
    user_prompt: str,
    schema_dict: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], List[str]]:
    error_context: Optional[str] = None
    last_ops: List[Dict[str, Any]] = []
    all_errors: List[str] = []

    for iteration in range(1, MAX_FEEDBACK_ITERATIONS + 2):
        logger.info(
            "Feedback loop iteração %d/%d",
            iteration,
            MAX_FEEDBACK_ITERATIONS + 1,
        )

        prompt_text = _build_ollama_prompt(
            user_prompt,
            schema_dict,
            error_context,
        )

        try:
            raw = _call_ollama(prompt_text)
        except RuntimeError as e:
            return [], [str(e)]

        try:
            diff = _parse_llm_json(raw)
        except ValueError as e:
            error_context = str(e)
            all_errors.append(error_context)
            continue

        ops = diff.get("operations")

        if ops is None and "op" in diff:
            ops = [diff]
        elif ops is None:
            error_context = f"JSON sem 'operations': {json.dumps(diff)[:200]}"
            all_errors.append(error_context)
            continue

        ok, errs = _validate_diff(ops)

        if ok:
            return ops, []

        error_context = "\n".join(errs)
        all_errors.extend(errs)
        last_ops = ops

        logger.warning("Iteração %d: Firewall bloqueou: %s", iteration, errs)

    return last_ops, all_errors


# ─────────────────────────────────────────────────────────────────────────────
# GOLDEN DATASET / TESTES
# ─────────────────────────────────────────────────────────────────────────────

def run_regression_tests() -> Dict[str, Any]:
    base_schema = {
        "objects": [
            {
                "name": "Cliente",
                "fields": [
                    {"name": "clienteid", "type": "integer"},
                    {"name": "nome", "type": "string"},
                ],
            },
            {
                "name": "Produto",
                "fields": [
                    {"name": "produtoid", "type": "integer"},
                    {"name": "nome", "type": "string"},
                ],
            },
        ],
        "relations": [
            {"from": "Cliente", "to": "Produto", "type": "ONE_TO_MANY"},
        ],
        "workspaces": [
            {
                "name": "Backoffice",
                "objects": ["Cliente", "Produto"],
                "primary_entity": "Cliente",
                "permissions": ["VER"],
            },
        ],
        "actions": [],
    }

    results: Dict[str, Any] = {}

    ops1 = [{"op": "ADD_FIELD", "object": "produto", "field": {"name": "preco", "type": "float"}}]
    ok1, _ = _validate_diff(ops1)
    bp1 = parse_blueprint(base_schema)
    _apply_operation(bp1, ops1[0])
    prod = _find_object(bp1, "Produto")
    campo = next((f for f in (prod.fields if prod else []) if f.name == "preco"), None)

    results["caso_1_add_field"] = {
        "passou": bool(ok1 and campo and campo.type == "float"),
        "tipo_criado": campo.type if campo else None,
    }

    ops2 = [{"op": "RENAME_OBJECT", "old_name": "cliente", "new_name": "Utilizador"}]
    ok2, _ = _validate_diff(ops2)
    bp2 = parse_blueprint(base_schema)
    _apply_operation(bp2, ops2[0])
    nomes = [o.name for o in bp2.objects]
    ws_obs = bp2.workspaces[0].objects if bp2.workspaces else []
    rel_from = bp2.relations[0].from_obj if bp2.relations else ""

    results["caso_2_rename_propagado"] = {
        "passou": bool(
            ok2
            and "Utilizador" in nomes
            and "Utilizador" in ws_obs
            and rel_from == "Utilizador"
        ),
        "workspace_atualizado": "Utilizador" in ws_obs,
        "relation_atualizada": rel_from == "Utilizador",
    }

    ops3 = [{"op": "ADD_TO_WORKSPACE", "workspace": "NaoExiste", "object": "produto"}]
    ok3, _ = _validate_diff(ops3)
    bp3 = parse_blueprint(base_schema)
    warns3 = _apply_operation(bp3, ops3[0])

    results["caso_3_ws_inexistente"] = {
        "passou": bool(any("não encontrado" in w for w in warns3) and len(bp3.workspaces) == 1),
        "warning": warns3,
    }

    raw_com_think = (
        '<think>\nVou adicionar o campo.\n{"x": 1}\n</think>\n'
        '{"operations": [{"op": "ADD_FIELD", "object": "produto", '
        '"field": {"name": "teste", "type": "string"}}]}'
    )

    try:
        parsed = _parse_llm_json(raw_com_think)
        results["caso_4_think_tags"] = {
            "passou": "operations" in parsed and parsed["operations"][0]["op"] == "ADD_FIELD",
        }
    except Exception as e:
        results["caso_4_think_tags"] = {"passou": False, "erro": str(e)}

    try:
        _parse_llm_json("")
        results["caso_5_string_vazia"] = {
            "passou": False,
            "erro": "devia ter lançado ValueError",
        }
    except ValueError as e:
        results["caso_5_string_vazia"] = {
            "passou": True,
            "mensagem": str(e)[:80],
        }

    bp4 = parse_blueprint({
        "objects": [
            {
                "name": "Livro",
                "fields": [
                    {"name": "livroid", "type": "integer"},
                    {"name": "titulo", "type": "string"},
                    {"name": "preco", "type": "float"},
                ],
            },
        ],
        "relations": [],
        "workspaces": [],
        "actions": [],
    })

    ops4 = _rule_based_operations("renomeia o campo preco para valor_unitario", bp4)
    results["caso_6_rule_rename_field"] = {
        "passou": bool(
            ops4
            and ops4[0]["op"] == "RENAME_FIELD"
            and ops4[0]["old_name"] == "preco"
            and ops4[0]["new_name"] == "valor_unitario"
        ),
        "ops": ops4,
    }

    ops5 = _rule_based_operations("remove o campo titulo", bp4)
    results["caso_7_rule_remove_field"] = {
        "passou": bool(ops5 and ops5[0]["op"] == "REMOVE_FIELD" and ops5[0]["field_name"] == "titulo"),
        "ops": ops5,
    }

    ops6 = _rule_based_operations("adiciona o campo stock como integer", bp4)
    results["caso_8_rule_add_field"] = {
        "passou": bool(
            ops6
            and ops6[0]["op"] == "ADD_FIELD"
            and ops6[0]["field"]["name"] == "stock"
            and ops6[0]["field"]["type"] == "integer"
        ),
        "ops": ops6,
    }

    bp5 = parse_blueprint({
        "objects": [
            {"name": "Trabalho", "fields": [{"name": "nome", "type": "string"}]},
            {"name": "Livro", "fields": [{"name": "titulo", "type": "string"}]},
        ],
        "relations": [],
        "workspaces": [],
        "actions": [],
    })

    ops7 = _rule_based_operations("cria uma ação AprovarTrabalho para o objeto Trabalho", bp5)
    results["caso_9_rule_add_action"] = {
        "passou": bool(
            len(ops7) == 1
            and ops7[0]["op"] == "ADD_ACTION"
            and ops7[0]["action"]["name"] == "AprovarTrabalho"
            and ops7[0]["action"]["entities_involved"] == ["Trabalho"]
        ),
        "ops": ops7,
    }

    _apply_operation(bp5, ops7[0])
    ops8 = _rule_based_operations("renomeia a ação AprovarTrabalho para ValidarTrabalho", bp5)
    results["caso_10_rule_rename_action"] = {
        "passou": bool(
            ops8
            and ops8[0]["op"] == "RENAME_ACTION"
            and ops8[0]["old_name"] == "AprovarTrabalho"
            and ops8[0]["new_name"] == "ValidarTrabalho"
        ),
        "ops": ops8,
    }

    _apply_operation(bp5, ops8[0])
    ops9 = _rule_based_operations("muda o trigger da ação ValidarTrabalho para automated", bp5)
    results["caso_11_rule_update_action_trigger"] = {
        "passou": bool(
            ops9
            and ops9[0]["op"] == "UPDATE_ACTION_TRIGGER"
            and ops9[0]["action"] == "ValidarTrabalho"
            and ops9[0]["trigger"] == "automated"
        ),
        "ops": ops9,
    }

    ops10 = _rule_based_operations("adiciona o objeto Livro à ação ValidarTrabalho", bp5)
    results["caso_12_rule_add_entity_to_action"] = {
        "passou": bool(
            ops10
            and ops10[0]["op"] == "ADD_ENTITY_TO_ACTION"
            and ops10[0]["action"] == "ValidarTrabalho"
            and ops10[0]["object"] == "Livro"
        ),
        "ops": ops10,
    }

    bp6 = parse_blueprint({
        "objects": [
            {"name": "Trabalho", "fields": [{"name": "nome", "type": "string"}]},
        ],
        "relations": [],
        "workspaces": [],
        "actions": [
            {
                "name": "ValidarTrabalho",
                "type": "DOMAIN_ACTION",
                "description": "Validar trabalho",
                "trigger": "manual",
                "entities_involved": ["Trabalho"],
                "steps": [],
                "preconditions": [],
                "postconditions": [],
            },
        ],
    })

    _apply_operation(bp6, {"op": "RENAME_OBJECT", "old_name": "Trabalho", "new_name": "ProjetoFinal"})
    action = _find_action(bp6, "ValidarTrabalho")
    results["caso_13_rename_object_propagates_actions"] = {
        "passou": bool(action and action.entities_involved == ["ProjetoFinal"]),
        "entities": action.entities_involved if action else [],
    }

    total = len(results)
    passed = sum(1 for r in results.values() if r.get("passou"))

    results["_sumario"] = {
        "total": total,
        "passou": passed,
        "taxa": f"{passed}/{total}",
    }

    return results


# ─────────────────────────────────────────────────────────────────────────────
# HANDLER PRINCIPAL
# ─────────────────────────────────────────────────────────────────────────────

def handle_update_schema(
    prompt: str,
    current_schema: Dict[str, Any],
    *,
    strict: bool = False,
) -> Dict[str, Any]:
    bp = parse_blueprint(current_schema if isinstance(current_schema, dict) else {})

    logger.info(
        "prompt='%s' | objects=%d | relations=%d | actions=%d | workspaces=%d",
        prompt,
        len(bp.objects),
        len(bp.relations),
        len(bp.actions),
        len(bp.workspaces),
    )

    operations = _rule_based_operations(prompt, bp)
    firewall_errors: List[str] = []

    if operations:
        logger.info("Operação simples detetada sem LLM: %s", operations)
    else:
        operations, firewall_errors = _run_with_feedback_loop(prompt, bp.to_dict())

    if not operations and firewall_errors:
        return _error_response(bp, firewall_errors)

    ok, remaining = _validate_diff(operations)

    if not ok:
        if strict:
            return _error_response(bp, remaining)

        clean = []
        for op in operations:
            s_ok, _ = _validate_diff([op])
            if s_ok:
                clean.append(op)

        logger.warning("Strict=False: %d/%d ops válidas", len(clean), len(operations))
        operations = clean

    bp_copy = parse_blueprint(bp.to_dict())
    applied: List[str] = []
    all_warns: List[str] = []

    for op_dict in operations:
        op_label = op_dict.get("op", "UNKNOWN")

        try:
            warns = _apply_operation(bp_copy, op_dict)
            all_warns.extend(warns)
            applied.append(op_label)
            logger.info("Aplicado: %s", op_label)

        except Exception as exc:
            msg = f"[{op_label}] Erro inesperado: {exc}"
            logger.exception(msg)
            all_warns.append(msg)

            if strict:
                return _error_response(bp, [msg])

    final = parse_blueprint(bp_copy.to_dict())

    logger.info(
        "Concluído: %d ops | objects=%d | relations=%d | actions=%d | workspaces=%d",
        len(applied),
        len(final.objects),
        len(final.relations),
        len(final.actions),
        len(final.workspaces),
    )

    return {
        "success": True,
        "type": "SYSTEM",
        "schema": final.to_dict(),
        "data": final.to_dict(),
        "mutations_applied": len(applied),
        "mutation_log": applied,
        "struct_warnings": all_warns,
        "errors": remaining if not ok else [],
    }


def _error_response(bp: BlueprintModel, errors: List[str]) -> Dict[str, Any]:
    logger.error("Erro no handler: %s", errors)

    return {
        "success": False,
        "type": "SYSTEM",
        "schema": bp.to_dict(),
        "data": bp.to_dict(),
        "mutations_applied": 0,
        "mutation_log": [],
        "struct_warnings": [],
        "errors": errors,
    }


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\nAiBizCore — Golden Dataset\n")
    r = run_regression_tests()

    for name, res in r.items():
        if name == "_sumario":
            continue

        print(f"{'✅' if res.get('passou') else '❌'} {name}: {res}")

    print(f"\n{r['_sumario']['taxa']}\n")
