# ===== handlers/update_schema_handler.py =====
# v4.2 — Compatibilidade total com /api/generate + /api/chat
#         Parser robusto para Llama 3.1 8B (<think> tags, texto livre)
#         Todos os campos RelationModel corrigidos (from_obj/to_obj)

from __future__ import annotations

import json
import logging
import re
import requests
from typing import Any, Dict, List, Optional, Tuple

from models import (
    BlueprintModel, FieldModel, ObjectModel,
    RelationModel, WorkspaceModel, parse_blueprint,
)

logger = logging.getLogger("update_schema_handler")
logging.basicConfig(level=logging.INFO, format="[%(name)s] %(levelname)s — %(message)s")

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURAÇÃO — lida do config.py (única fonte da verdade)
# ─────────────────────────────────────────────────────────────────────────────

try:
    from config import MODELS, OPTIONS, OPTIONS_8B, OLLAMA_URL as _CFG_URL, MODEL_TIER
    OLLAMA_MODEL   = MODELS.get("update_schema", MODELS.get("router", "llama3.2:3b"))
    OLLAMA_OPTIONS = OPTIONS_8B if MODEL_TIER == "8b" else OPTIONS
    OLLAMA_BASE    = _CFG_URL  # ex: "http://localhost:11434/api/generate"
except ImportError:
    logger.warning("config.py não encontrado — a usar defaults")
    OLLAMA_MODEL   = "llama3.2:3b"
    OLLAMA_OPTIONS = {"temperature": 0.1, "num_ctx": 2048, "num_predict": 1024}
    OLLAMA_BASE    = "http://localhost:11434/api/generate"

# Derivar os dois endpoints a partir da base configurada
# Independentemente de o utilizador ter mudado para /api/chat ou /api/generate,
# o código detecta e adapta automaticamente.
_BASE = OLLAMA_BASE.replace("/api/generate", "").replace("/api/chat", "")
ENDPOINT_GENERATE = f"{_BASE}/api/generate"
ENDPOINT_CHAT     = f"{_BASE}/api/chat"

OLLAMA_TIMEOUT = 180

# ─────────────────────────────────────────────────────────────────────────────
# OPERAÇÕES VÁLIDAS
# ─────────────────────────────────────────────────────────────────────────────

VALID_OPS = {
    "ADD_OBJECT", "REMOVE_OBJECT", "RENAME_OBJECT",
    "ADD_FIELD", "REMOVE_FIELD", "RENAME_FIELD", "RETYPE_FIELD",
    "ADD_RELATION", "REMOVE_RELATION",
    "ADD_WORKSPACE", "REMOVE_WORKSPACE",
    "ADD_TO_WORKSPACE", "REMOVE_FROM_WORKSPACE",
}

# ─────────────────────────────────────────────────────────────────────────────
# FEW-SHOT EXAMPLES
# ─────────────────────────────────────────────────────────────────────────────

FEW_SHOT_EXAMPLES = """
=== EXEMPLOS (Few-Shot) ===

EXEMPLO 1 — Adicionar campo:
  Input: "Adiciona campo preco ao objeto produto"
  Output: {"operations": [{"op": "ADD_FIELD", "object": "produto", "field": {"name": "preco", "type": "float"}}]}

EXEMPLO 2 — Renomear objeto (propaga para workspaces e relations automaticamente):
  Input: "Renomeia cliente para utilizador"
  Output: {"operations": [{"op": "RENAME_OBJECT", "old_name": "cliente", "new_name": "utilizador"}]}

EXEMPLO 3 — Adicionar objeto:
  Input: "Cria o objeto Fornecedor com nome e email"
  Output: {"operations": [{"op": "ADD_OBJECT", "object": {"name": "Fornecedor", "fields": [{"name": "nome", "type": "string"}, {"name": "email", "type": "string"}]}}]}

EXEMPLO 4 — Workspace inexistente (criar antes de adicionar):
  Input: "Adiciona produto ao workspace Vendas" (Vendas não existe)
  Output: {"operations": [{"op": "ADD_WORKSPACE", "workspace": {"name": "Vendas", "objects": ["produto"], "permissions": ["VER"]}}, {"op": "ADD_TO_WORKSPACE", "workspace": "Vendas", "object": "produto"}]}

EXEMPLO 5 — Relação entre objetos:
  Input: "Liga Cliente a Encomenda com ONE_TO_MANY"
  Output: {"operations": [{"op": "ADD_RELATION", "relation": {"from": "Cliente", "to": "Encomenda", "type": "ONE_TO_MANY"}}]}

=== FIM DOS EXEMPLOS ===
"""

_SYSTEM_PROMPT = f"""\
És um gerador de diffs JSON para um sistema de schemas de negócio.
Recebes uma instrução e o schema atual em JSON.
Devolves APENAS um objeto JSON válido com a chave "operations".

REGRAS ABSOLUTAS:
1. APENAS JSON puro. ZERO texto. ZERO explicações. ZERO markdown (```json).
2. Não uses <think> ou qualquer bloco de raciocínio. Responde diretamente.
3. Objetos têm "fields". Workspaces têm "objects". NUNCA os confundas.
4. ADD_FIELD: "field" é sempre um dict único, nunca uma lista.
5. ADD_RELATION: o payload tem "from", "to", "type" — não "from_obj" nem "from_object".
6. Verifica se o workspace/objeto existe no schema antes de o referenciar.

OPERAÇÕES VÁLIDAS:
ADD_OBJECT, REMOVE_OBJECT, RENAME_OBJECT,
ADD_FIELD, REMOVE_FIELD, RENAME_FIELD, RETYPE_FIELD,
ADD_RELATION, REMOVE_RELATION,
ADD_WORKSPACE, REMOVE_WORKSPACE, ADD_TO_WORKSPACE, REMOVE_FROM_WORKSPACE

{FEW_SHOT_EXAMPLES}
"""

# ─────────────────────────────────────────────────────────────────────────────
# LIMPEZA DE OUTPUT DO LLM
# ─────────────────────────────────────────────────────────────────────────────

def _strip_llm_noise(text: str) -> str:
    """
    Remove tudo o que não é JSON da resposta do LLM.

    Por ordem:
      1. Remove blocos <think>...</think> (Llama 3.1 8B com raciocínio activado)
      2. Remove markdown fences ```json ... ```
      3. Remove texto antes do primeiro { e depois do último }
    """
    if not text:
        return ""

    # 1. Remover blocos <think> (podem conter JSON falso que engana o parser)
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)

    # 2. Remover markdown fences
    text = re.sub(r"```(?:json)?\s*", "", text)
    text = re.sub(r"```", "", text)

    # 3. Isolar o bloco JSON principal — apanhar do primeiro { ao último }
    # Usamos um extractor balanceado em vez de greedy para lidar com
    # JSON nested sem corrupção
    text = text.strip()
    start = text.find("{")
    end   = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start:end + 1]

    return text.strip()


def _extract_balanced_json(text: str) -> Optional[str]:
    """
    Extrai o primeiro bloco JSON balanceado (contagem de { e }).
    Mais robusto que re.search greedy quando há texto entre chaves.
    """
    depth   = 0
    start   = None
    in_str  = False
    escape  = False

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
    """
    Converte o output bruto do LLM num dict Python.
    4 estratégias progressivas — robustez total para 3B e 8B.
    """
    if not raw_text or not raw_text.strip():
        raise ValueError("Resposta vazia do LLM — possível incompatibilidade de endpoint ou timeout.")

    # Estratégia 1: parse direto (JSON puro — caminho feliz)
    try:
        return json.loads(raw_text.strip())
    except json.JSONDecodeError:
        pass

    # Estratégia 2: limpar noise (think tags, markdown) e tentar de novo
    cleaned = _strip_llm_noise(raw_text)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Estratégia 3: extractor balanceado (mais preciso que greedy regex)
    candidate = _extract_balanced_json(cleaned or raw_text)
    if candidate:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

    # Estratégia 4: regex greedy como último recurso
    brace_match = re.search(r"\{.*\}", cleaned or raw_text, re.DOTALL)
    if brace_match:
        try:
            return json.loads(brace_match.group(0))
        except json.JSONDecodeError:
            pass

    raise ValueError(
        f"Não foi possível extrair JSON válido da resposta do LLM.\n"
        f"Raw (primeiros 400 chars): {raw_text[:400]}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# CHAMADA AO OLLAMA — suporte automático a /api/generate E /api/chat
# ─────────────────────────────────────────────────────────────────────────────

def _call_ollama(prompt_text: str, temperature: Optional[float] = None) -> str:
    """
    Tenta /api/generate primeiro (formato mais simples e universal).
    Se receber 404 ou erro de formato, tenta /api/chat automaticamente.
    Nunca devolve string vazia sem lançar excepção — garante que o caller
    sempre tem texto ou um RuntimeError explicativo.
    """
    temp = temperature if temperature is not None else float(OLLAMA_OPTIONS.get("temperature", 0.1))
    options = {**OLLAMA_OPTIONS, "temperature": temp}

    # ── Tentativa 1: /api/generate ──────────────────────────────────────────
    result = _try_generate(prompt_text, options)
    if result is not None:
        return result

    # ── Tentativa 2: /api/chat (fallback se o utilizador mudou o endpoint) ──
    logger.warning("/api/generate falhou ou devolveu vazio — a tentar /api/chat")
    result = _try_chat(prompt_text, options)
    if result is not None:
        return result

    raise RuntimeError(
        f"Ollama não respondeu em nenhum endpoint "
        f"({ENDPOINT_GENERATE}, {ENDPOINT_CHAT}). "
        f"Verifica se o modelo '{OLLAMA_MODEL}' está instalado: ollama list"
    )


def _try_generate(prompt_text: str, options: dict) -> Optional[str]:
    """Chama /api/generate. Devolve texto limpo ou None em caso de falha."""
    payload = {
        "model":   OLLAMA_MODEL,
        "prompt":  prompt_text,
        "stream":  False,
        "options": options,
    }
    try:
        resp = requests.post(ENDPOINT_GENERATE, json=payload, timeout=OLLAMA_TIMEOUT)
        resp.raise_for_status()
        raw = resp.json().get("response") or ""
        if raw.strip():
            logger.debug("[generate] raw (400 chars): %s", raw[:400])
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
        raise RuntimeError(
            f"Timeout ({OLLAMA_TIMEOUT}s) em /api/generate. "
            "Modelo demasiado lento para o hardware — considera reduzir num_ctx."
        )
    except Exception as e:
        logger.warning("[generate] erro inesperado: %s", e)
        return None


def _try_chat(prompt_text: str, options: dict) -> Optional[str]:
    """Chama /api/chat. Devolve texto limpo ou None em caso de falha."""
    payload = {
        "model":    OLLAMA_MODEL,
        "messages": [{"role": "user", "content": prompt_text}],
        "stream":   False,
        "options":  options,
    }
    try:
        resp = requests.post(ENDPOINT_CHAT, json=payload, timeout=OLLAMA_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        # /api/chat devolve: {"message": {"role": "assistant", "content": "..."}}
        raw = (data.get("message") or {}).get("content") or ""
        if raw.strip():
            logger.debug("[chat] raw (400 chars): %s", raw[:400])
            return _strip_llm_noise(raw)
        logger.warning("[chat] resposta vazia")
        return None
    except requests.exceptions.HTTPError as e:
        logger.warning("[chat] HTTP erro: %s", e)
        return None
    except Exception as e:
        logger.warning("[chat] erro inesperado: %s", e)
        return None


def _build_ollama_prompt(
    user_prompt: str,
    current_schema: Dict[str, Any],
    error_context: Optional[str] = None,
) -> str:
    schema_str = json.dumps(current_schema, ensure_ascii=False, indent=2)
    base = f"{_SYSTEM_PROMPT}\n\nSCHEMA ATUAL:\n{schema_str}\n\n"
    if error_context:
        base += (
            f"⚠️  ÚLTIMA OPERAÇÃO FALHOU:\n{error_context}\n\n"
            f"Corrige APENAS este erro e gera a operação correta.\n\n"
        )
    base += f"INSTRUÇÃO: {user_prompt}\n\nJSON DIFF:"
    return base


# ─────────────────────────────────────────────────────────────────────────────
# FIREWALL
# ─────────────────────────────────────────────────────────────────────────────

def _validate_diff(operations: List[Dict[str, Any]]) -> Tuple[bool, List[str]]:
    errors: List[str] = []

    if not isinstance(operations, list):
        return False, [f"'operations' deve ser lista, recebeu {type(operations).__name__}"]

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

        if op == "ADD_OBJECT":
            p = op_dict.get("object") or {}
            if not isinstance(p, dict):
                errors.append(f"[op #{i}] ADD_OBJECT.object deve ser dict.")
            elif "objects" in p and "fields" not in p:
                errors.append(f"[op #{i}] ADD_OBJECT parece Workspace (tem 'objects', não tem 'fields').")
            elif "from" in p or "to" in p:
                errors.append(f"[op #{i}] ADD_OBJECT parece Relation.")

        if op == "ADD_WORKSPACE":
            p = op_dict.get("workspace") or {}
            if not isinstance(p, dict):
                errors.append(f"[op #{i}] ADD_WORKSPACE.workspace deve ser dict.")
            elif "fields" in p:
                errors.append(f"[op #{i}] ADD_WORKSPACE tem 'fields' — parece Object.")

        required: Dict[str, List[str]] = {
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
        for key in required.get(op, []):
            if key not in op_dict:
                errors.append(f"[op #{i}] '{op}' falta chave '{key}'.")

        if op == "ADD_FIELD":
            if isinstance(op_dict.get("field"), list):
                errors.append(f"[op #{i}] ADD_FIELD.field é lista — usa ADD_FIELD separados.")

        if op == "ADD_RELATION":
            rel = op_dict.get("relation") or {}
            if isinstance(rel, dict) and ("from" not in rel or "to" not in rel):
                errors.append(f"[op #{i}] ADD_RELATION.relation precisa de 'from' e 'to'.")

    return len(errors) == 0, errors


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS DE MUTAÇÃO — todos usam from_obj/to_obj (alinhados com models.py)
# ─────────────────────────────────────────────────────────────────────────────

def _find_object(bp: BlueprintModel, name: str) -> Optional[ObjectModel]:
    nl = name.lower()
    return next((o for o in bp.objects if o.name.lower() == nl), None)


def _find_workspace(bp: BlueprintModel, name: str) -> Optional[WorkspaceModel]:
    nl = name.lower()
    return next((w for w in bp.workspaces if w.name.lower() == nl), None)


def _remove_object_from_all_workspaces(bp: BlueprintModel, name: str) -> None:
    nl = name.lower()
    for ws in bp.workspaces:
        ws.objects = [o for o in ws.objects if o.lower() != nl]


def _rename_object_in_workspaces(bp: BlueprintModel, old: str, new: str) -> None:
    ol = old.lower()
    for ws in bp.workspaces:
        ws.objects = [new if o.lower() == ol else o for o in ws.objects]
        if ws.primary_entity.lower() == ol:
            ws.primary_entity = new


def _rename_object_in_relations(bp: BlueprintModel, old: str, new: str) -> None:
    # FIX: RelationModel usa from_obj/to_obj (não from_object/to_object)
    ol = old.lower()
    for rel in bp.relations:
        if rel.from_obj.lower() == ol:
            rel.from_obj = new
        if rel.to_obj.lower() == ol:
            rel.to_obj = new


def _remove_object_from_relations(bp: BlueprintModel, name: str) -> None:
    # FIX: RelationModel usa from_obj/to_obj
    nl = name.lower()
    bp.relations = [
        r for r in bp.relations
        if r.from_obj.lower() != nl and r.to_obj.lower() != nl
    ]


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
            w.append(f"ADD_OBJECT: payload inválido — {e}"); return w
        if _find_object(bp, obj.name):
            w.append(f"ADD_OBJECT: '{obj.name}' já existe.")
        else:
            bp.objects.append(obj)

    elif op == "REMOVE_OBJECT":
        name = op_dict["name"]; nl = name.lower(); n0 = len(bp.objects)
        bp.objects = [o for o in bp.objects if o.name.lower() != nl]
        if len(bp.objects) == n0:
            w.append(f"REMOVE_OBJECT: '{name}' não encontrado.")
        else:
            _remove_object_from_all_workspaces(bp, name)
            _remove_object_from_relations(bp, name)

    elif op == "RENAME_OBJECT":
        old, new = op_dict["old_name"], op_dict["new_name"]
        obj = _find_object(bp, old)
        if obj is None:
            w.append(f"RENAME_OBJECT: '{old}' não encontrado.")
        elif _find_object(bp, new):
            w.append(f"RENAME_OBJECT: '{new}' já existe.")
        else:
            obj.name = new
            _rename_object_in_workspaces(bp, old, new)
            _rename_object_in_relations(bp, old, new)

    elif op == "ADD_FIELD":
        obj = _find_object(bp, op_dict["object"])
        if obj is None:
            w.append(f"ADD_FIELD: objeto '{op_dict['object']}' não encontrado."); return w
        fp = op_dict["field"]
        try:
            f = FieldModel.model_validate(fp) if isinstance(fp, dict) else FieldModel(name=str(fp))
        except Exception as e:
            w.append(f"ADD_FIELD: field inválido — {e}"); return w
        if any(x.name.lower() == f.name.lower() for x in obj.fields):
            w.append(f"ADD_FIELD: '{f.name}' já existe em '{obj.name}'.")
        else:
            obj.fields.append(f)

    elif op == "REMOVE_FIELD":
        obj = _find_object(bp, op_dict["object"])
        if obj is None:
            w.append(f"REMOVE_FIELD: objeto '{op_dict['object']}' não encontrado."); return w
        fn = op_dict["field_name"].lower(); n0 = len(obj.fields)
        obj.fields = [f for f in obj.fields if f.name.lower() != fn]
        if len(obj.fields) == n0:
            w.append(f"REMOVE_FIELD: campo '{op_dict['field_name']}' não encontrado.")

    elif op == "RENAME_FIELD":
        obj = _find_object(bp, op_dict["object"])
        if obj is None:
            w.append(f"RENAME_FIELD: objeto '{op_dict['object']}' não encontrado."); return w
        tgt = next((f for f in obj.fields if f.name.lower() == op_dict["old_name"].lower()), None)
        if tgt is None:
            w.append(f"RENAME_FIELD: campo '{op_dict['old_name']}' não encontrado.")
        elif any(f.name.lower() == op_dict["new_name"].lower() and f is not tgt for f in obj.fields):
            w.append(f"RENAME_FIELD: '{op_dict['new_name']}' já existe.")
        else:
            tgt.name = op_dict["new_name"]

    elif op == "RETYPE_FIELD":
        obj = _find_object(bp, op_dict["object"])
        if obj is None:
            w.append(f"RETYPE_FIELD: objeto '{op_dict['object']}' não encontrado."); return w
        tgt = next((f for f in obj.fields if f.name.lower() == op_dict["field_name"].lower()), None)
        if tgt is None:
            w.append(f"RETYPE_FIELD: campo '{op_dict['field_name']}' não encontrado.")
        else:
            tgt.type = str(op_dict["new_type"])

    elif op == "ADD_RELATION":
        rp = op_dict.get("relation") or {}
        if not isinstance(rp, dict):
            w.append("ADD_RELATION: payload deve ser dict."); return w
        try:
            rel = RelationModel.model_validate(rp)
        except Exception as e:
            w.append(f"ADD_RELATION: payload inválido — {e}"); return w
        # FIX: usa from_obj/to_obj (não from_object/to_object)
        if not _find_object(bp, rel.from_obj):
            w.append(f"ADD_RELATION: '{rel.from_obj}' não encontrado.")
        elif not _find_object(bp, rel.to_obj):
            w.append(f"ADD_RELATION: '{rel.to_obj}' não encontrado.")
        elif any(r.from_obj.lower() == rel.from_obj.lower() and r.to_obj.lower() == rel.to_obj.lower()
                 for r in bp.relations):
            w.append(f"ADD_RELATION: '{rel.from_obj}→{rel.to_obj}' já existe.")
        else:
            bp.relations.append(rel)

    elif op == "REMOVE_RELATION":
        # RelationModel não tem 'name' — interpreta como "From→To"
        name = op_dict["name"]; n0 = len(bp.relations)
        parts = re.split(r"[→_\-]", name, maxsplit=1)
        if len(parts) == 2:
            fl, tl = parts[0].strip().lower(), parts[1].strip().lower()
            bp.relations = [r for r in bp.relations
                            if not (r.from_obj.lower() == fl and r.to_obj.lower() == tl)]
        else:
            bp.relations = [r for r in bp.relations if r.from_obj.lower() != name.lower()]
        if len(bp.relations) == n0:
            w.append(f"REMOVE_RELATION: '{name}' não encontrada.")

    elif op == "ADD_WORKSPACE":
        p = op_dict.get("workspace") or {}
        try:
            ws = WorkspaceModel.model_validate(p) if p else WorkspaceModel(name="unnamed_ws")
        except Exception as e:
            w.append(f"ADD_WORKSPACE: payload inválido — {e}"); return w
        if _find_workspace(bp, ws.name):
            w.append(f"ADD_WORKSPACE: '{ws.name}' já existe.")
        else:
            valid = {o.name.lower() for o in bp.objects}
            dropped = set(ws.objects) - {o for o in ws.objects if o.lower() in valid}
            ws.objects = [o for o in ws.objects if o.lower() in valid]
            if dropped:
                w.append(f"ADD_WORKSPACE: removidas refs inexistentes: {dropped}")
            bp.workspaces.append(ws)

    elif op == "REMOVE_WORKSPACE":
        name = op_dict["name"]; nl = name.lower(); n0 = len(bp.workspaces)
        bp.workspaces = [ws for ws in bp.workspaces if ws.name.lower() != nl]
        if len(bp.workspaces) == n0:
            w.append(f"REMOVE_WORKSPACE: '{name}' não encontrado.")

    elif op == "ADD_TO_WORKSPACE":
        ws  = _find_workspace(bp, op_dict["workspace"])
        obj = _find_object(bp, op_dict["object"])
        if ws is None:
            w.append(f"ADD_TO_WORKSPACE: workspace '{op_dict['workspace']}' não encontrado.")
        elif obj is None:
            w.append(f"ADD_TO_WORKSPACE: objeto '{op_dict['object']}' não encontrado.")
        elif obj.name not in ws.objects:
            ws.objects.append(obj.name)

    elif op == "REMOVE_FROM_WORKSPACE":
        ws = _find_workspace(bp, op_dict["workspace"])
        if ws is None:
            w.append(f"REMOVE_FROM_WORKSPACE: workspace '{op_dict['workspace']}' não encontrado.")
        else:
            ol = op_dict["object"].lower(); n0 = len(ws.objects)
            ws.objects = [o for o in ws.objects if o.lower() != ol]
            if len(ws.objects) == n0:
                w.append(f"REMOVE_FROM_WORKSPACE: '{op_dict['object']}' não encontrado em '{op_dict['workspace']}'.")

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
        logger.info("Feedback loop iteração %d/%d", iteration, MAX_FEEDBACK_ITERATIONS + 1)

        prompt_text = _build_ollama_prompt(user_prompt, schema_dict, error_context)

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
# GOLDEN DATASET — Regressão inline
# ─────────────────────────────────────────────────────────────────────────────

def run_regression_tests() -> Dict[str, Any]:
    base_schema = {
        "objects": [
            {"name": "Cliente", "fields": [{"name": "clienteid", "type": "integer"}, {"name": "nome", "type": "string"}]},
            {"name": "Produto",  "fields": [{"name": "produtoid",  "type": "integer"}, {"name": "nome", "type": "string"}]},
        ],
        "relations": [{"from": "Cliente", "to": "Produto", "type": "ONE_TO_MANY"}],
        "workspaces": [{"name": "Backoffice", "objects": ["Cliente", "Produto"], "primary_entity": "Cliente", "permissions": ["VER"]}],
        "actions": [],
    }

    results = {}

    # Caso 1: ADD_FIELD com tipo float
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

    # Caso 2: RENAME com propagação
    ops2 = [{"op": "RENAME_OBJECT", "old_name": "cliente", "new_name": "utilizador"}]
    ok2, _ = _validate_diff(ops2)
    bp2 = parse_blueprint(base_schema)
    _apply_operation(bp2, ops2[0])
    nomes = [o.name.lower() for o in bp2.objects]
    ws_obs = [o.lower() for o in (bp2.workspaces[0].objects if bp2.workspaces else [])]
    rel_from = bp2.relations[0].from_obj.lower() if bp2.relations else ""
    results["caso_2_rename_propagado"] = {
        "passou": bool(ok2 and "utilizador" in nomes and "utilizador" in ws_obs and rel_from == "utilizador"),
        "workspace_atualizado": "utilizador" in ws_obs,
        "relation_atualizada":  rel_from == "utilizador",
    }

    # Caso 3: workspace inexistente gera warning sem crash
    ops3 = [{"op": "ADD_TO_WORKSPACE", "workspace": "NaoExiste", "object": "produto"}]
    ok3, _ = _validate_diff(ops3)
    bp3 = parse_blueprint(base_schema)
    warns3 = _apply_operation(bp3, ops3[0])
    results["caso_3_ws_inexistente"] = {
        "passou": bool(any("não encontrado" in w for w in warns3) and len(bp3.workspaces) == 1),
        "warning": warns3,
    }

    # Caso 4: parser resiste a <think> tags do 8B
    raw_com_think = '<think>\nVou adicionar o campo.\n{"x": 1}\n</think>\n{"operations": [{"op": "ADD_FIELD", "object": "produto", "field": {"name": "teste", "type": "string"}}]}'
    try:
        parsed = _parse_llm_json(raw_com_think)
        results["caso_4_think_tags"] = {
            "passou": "operations" in parsed and parsed["operations"][0]["op"] == "ADD_FIELD",
        }
    except Exception as e:
        results["caso_4_think_tags"] = {"passou": False, "erro": str(e)}

    # Caso 5: string vazia do LLM → excepção explicativa (não NoneType)
    try:
        _parse_llm_json("")
        results["caso_5_string_vazia"] = {"passou": False, "erro": "devia ter lançado ValueError"}
    except ValueError as e:
        results["caso_5_string_vazia"] = {"passou": True, "mensagem": str(e)[:80]}

    total  = len(results)
    passed = sum(1 for r in results.values() if r.get("passou"))
    results["_sumario"] = {"total": total, "passou": passed, "taxa": f"{passed}/{total}"}
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
    logger.info("prompt='%s' | objects=%d | relations=%d | workspaces=%d",
                prompt, len(bp.objects), len(bp.relations), len(bp.workspaces))

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

    bp_copy    = parse_blueprint(bp.to_dict())
    applied: List[str]  = []
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
    logger.info("Concluído: %d ops | objects=%d | relations=%d | workspaces=%d",
                len(applied), len(final.objects), len(final.relations), len(final.workspaces))

    return {
        "success":          True,
        "type":             "SYSTEM",
        "schema":           final.to_dict(),
        "data":             final.to_dict(),
        "mutations_applied": len(applied),
        "mutation_log":     applied,
        "struct_warnings":  all_warns,
        "errors":           remaining if not ok else [],
    }


def _error_response(bp: BlueprintModel, errors: List[str]) -> Dict[str, Any]:
    logger.error("Erro no handler: %s", errors)
    return {
        "success":          False,
        "type":             "SYSTEM",
        "schema":           bp.to_dict(),
        "data":             bp.to_dict(),
        "mutations_applied": 0,
        "mutation_log":     [],
        "struct_warnings":  [],
        "errors":           errors,
    }


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n🧪 AiBizCore — Golden Dataset\n")
    r = run_regression_tests()
    for name, res in r.items():
        if name == "_sumario": continue
        print(f"{'✅' if res.get('passou') else '❌'} {name}: {res}")
    print(f"\n📊 {r['_sumario']['taxa']}\n")
