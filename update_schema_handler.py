# ===== handlers/update_schema_handler.py =====
# AiBizCore — v4.1 Production-Grade Rewrite
#
# FIXES aplicados nesta versão:
#   • RelationModel usa from_obj/to_obj (não from_object/to_object) — alinhado com models.py
#   • RelationModel não tem campo 'name' — removidas todas as referências
#   • Few-Shot Prompting injetado em CADA chamada ao Ollama
#   • Feedback Loop automático: se a Firewall detetar erros, reenvia ao Ollama com contexto
#   • Regex de limpeza de markdown obrigatória na saída do LLM
#   • Golden Dataset como testes de regressão inline (run_regression_tests())
#   • Logging estruturado — zero erros 500 silenciosos

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

logger = logging.getLogger("update_schema_handler")
logging.basicConfig(
    level=logging.INFO,
    format="[%(name)s] %(levelname)s — %(message)s",
)

# ---------------------------------------------------------------------------
# Configuração Ollama — lida do config.py (única fonte da verdade)
# ---------------------------------------------------------------------------

from config import MODELS, OPTIONS_8B, OLLAMA_URL as _OLLAMA_URL

OLLAMA_URL     = _OLLAMA_URL
OLLAMA_MODEL   = MODELS["update_schema"]   # llama3.1:8b-instruct-q4_K_M
OLLAMA_TIMEOUT = 180   # o 8B é mais lento que o 3B — margem extra

# ---------------------------------------------------------------------------
# Operações válidas
# ---------------------------------------------------------------------------

VALID_OPS = {
    "ADD_OBJECT", "REMOVE_OBJECT", "RENAME_OBJECT",
    "ADD_FIELD", "REMOVE_FIELD", "RENAME_FIELD", "RETYPE_FIELD",
    "ADD_RELATION", "REMOVE_RELATION",
    "ADD_WORKSPACE", "REMOVE_WORKSPACE",
    "ADD_TO_WORKSPACE", "REMOVE_FROM_WORKSPACE",
}

# ---------------------------------------------------------------------------
# FEW-SHOT EXAMPLES — injetados em CADA prompt enviado ao Ollama
# Cobrem: adição, renomeação, falha de validação, workspace inexistente
# ---------------------------------------------------------------------------

FEW_SHOT_EXAMPLES = """
=== EXEMPLOS DE REFERÊNCIA (Few-Shot) ===

EXEMPLO 1 — Adição de campo:
  Instrução: "Adiciona campo preco ao objeto produto"
  Resposta correta:
  {"operations": [{"op": "ADD_FIELD", "object": "produto", "field": {"name": "preco", "type": "float"}}]}

EXEMPLO 2 — Renomeação de objeto (o sistema trata automaticamente as referências em workspaces/relations):
  Instrução: "Renomeia cliente para utilizador"
  Resposta correta:
  {"operations": [{"op": "RENAME_OBJECT", "old_name": "cliente", "new_name": "utilizador"}]}

EXEMPLO 3 — Adicionar objeto com campos:
  Instrução: "Cria o objeto Fornecedor com nome e email"
  Resposta correta:
  {"operations": [{"op": "ADD_OBJECT", "object": {"name": "Fornecedor", "fields": [{"name": "nome", "type": "string"}, {"name": "email", "type": "string"}]}}]}

EXEMPLO 4 — Workspace inexistente (deves verificar que o workspace existe antes de sugerir ADD_TO_WORKSPACE):
  Instrução: "Adiciona produto ao workspace Vendas" (quando Vendas não existe)
  Resposta correta:
  {"operations": [{"op": "ADD_WORKSPACE", "workspace": {"name": "Vendas", "objects": ["produto"], "permissions": ["VER", "CRIAR"]}}, {"op": "ADD_TO_WORKSPACE", "workspace": "Vendas", "object": "produto"}]}

EXEMPLO 5 — Remoção de campo:
  Instrução: "Remove o campo telefone do cliente"
  Resposta correta:
  {"operations": [{"op": "REMOVE_FIELD", "object": "cliente", "field_name": "telefone"}]}

EXEMPLO 6 — Relação entre objetos:
  Instrução: "Adiciona relação ONE_TO_MANY de Cliente para Encomenda"
  Resposta correta:
  {"operations": [{"op": "ADD_RELATION", "relation": {"from": "Cliente", "to": "Encomenda", "type": "ONE_TO_MANY"}}]}

=== FIM DOS EXEMPLOS ===
"""

_SYSTEM_PROMPT = f"""\
És um gerador de diffs JSON para um sistema de schemas de negócio.
Recebes uma instrução do utilizador e o schema atual (JSON).
Deves devolver APENAS um objeto JSON válido com a chave "operations".
"operations" é uma lista de mudanças cirúrgicas a aplicar ao schema.

REGRAS ABSOLUTAS — NUNCA violes estas:
1. Devolve APENAS JSON puro e cru. ZERO texto. ZERO explicações. ZERO blocos markdown (```json).
2. Se precisares de escrever algo além do JSON, NÃO O FAÇAS. Escreve apenas o JSON.
3. Objetos têm "fields" (lista). Workspaces têm "objects" (lista de nomes). NUNCA os confundas.
4. Um Workspace NÃO é um Object. Nunca coloques um Workspace dentro de ADD_OBJECT.
5. Uma Relation tem "from", "to", "type". NUNCA dentro de ADD_OBJECT.
6. Para ADD_FIELD, "field" deve ser um dict único, NUNCA uma lista.
7. Verifica sempre se o workspace/objeto referenciado existe no schema antes de o usar.

OPERAÇÕES VÁLIDAS:
ADD_OBJECT, REMOVE_OBJECT, RENAME_OBJECT,
ADD_FIELD, REMOVE_FIELD, RENAME_FIELD, RETYPE_FIELD,
ADD_RELATION, REMOVE_RELATION,
ADD_WORKSPACE, REMOVE_WORKSPACE,
ADD_TO_WORKSPACE, REMOVE_FROM_WORKSPACE

{FEW_SHOT_EXAMPLES}
"""

# ---------------------------------------------------------------------------
# CHAMADA AO OLLAMA com limpeza obrigatória de markdown
# ---------------------------------------------------------------------------

def _strip_markdown(text: str) -> str:
    """Remove blocos ```json ... ``` e qualquer lixo antes/depois do JSON."""
    # Remover fences de markdown
    text = re.sub(r"```(?:json)?\s*", "", text)
    text = re.sub(r"```", "", text)
    return text.strip()


def _call_ollama(prompt_text: str, temperature: float = 0.1) -> str:
    """
    Envia o prompt ao Ollama e devolve o texto bruto limpo.
    Lança RuntimeError detalhado se a chamada falhar.
    """
    payload = {
        "model":  OLLAMA_MODEL,
        "prompt": prompt_text,
        "stream": False,
        "options": {
            **OPTIONS_8B,
            "temperature": temperature,  # permite override pontual
        },
    }

    try:
        resp = requests.post(OLLAMA_URL, json=payload, timeout=OLLAMA_TIMEOUT)
        resp.raise_for_status()
    except requests.exceptions.ConnectionError:
        raise RuntimeError(
            f"[update_schema_handler] Ollama inacessível em {OLLAMA_URL}. "
            "Verifica se o serviço está em execução (ollama serve)."
        )
    except requests.exceptions.Timeout:
        raise RuntimeError(
            f"[update_schema_handler] Timeout ao chamar o Ollama ({OLLAMA_TIMEOUT}s). "
            "Considera reduzir num_predict ou aumentar OLLAMA_TIMEOUT."
        )
    except requests.exceptions.HTTPError as e:
        raise RuntimeError(f"[update_schema_handler] Ollama devolveu erro HTTP: {e}")

    raw_text = resp.json().get("response") or ""
    cleaned = _strip_markdown(raw_text)
    logger.debug("Ollama raw (primeiros 400 chars): %s", raw_text[:400])
    return cleaned


def _parse_llm_json(raw_text: str) -> Dict[str, Any]:
    """
    Extrai e faz parse do JSON da resposta bruta do LLM.
    Tenta 3 estratégias progressivas.
    """
    # Estratégia 1: parse direto (caminho feliz)
    try:
        return json.loads(raw_text.strip())
    except json.JSONDecodeError:
        pass

    # Estratégia 2: extrair bloco ```json se o modelo ignorou as instruções
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


def _build_ollama_prompt(
    user_prompt: str,
    current_schema: Dict[str, Any],
    error_context: Optional[str] = None,
) -> str:
    """
    Constrói o prompt final com few-shot e, opcionalmente, contexto de erro
    para o Feedback Loop automático.
    """
    schema_str = json.dumps(current_schema, ensure_ascii=False, indent=2)

    base = (
        f"{_SYSTEM_PROMPT}\n\n"
        f"SCHEMA ATUAL:\n{schema_str}\n\n"
    )

    if error_context:
        # FEEDBACK LOOP: o erro da iteração anterior é devolvido ao Llama
        base += (
            f"⚠️  A TUA ÚLTIMA OPERAÇÃO FALHOU COM O SEGUINTE ERRO:\n"
            f"{error_context}\n\n"
            f"Corrige APENAS este erro e gera a operação correta.\n\n"
        )

    base += f"INSTRUÇÃO DO UTILIZADOR: {user_prompt}\n\nJSON DIFF:"
    return base


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
    """
    errors: List[str] = []

    if not isinstance(operations, list):
        return False, ["'operations' deve ser uma lista, recebeu: " + type(operations).__name__]

    for i, op_dict in enumerate(operations):
        if not isinstance(op_dict, dict):
            errors.append(f"[op #{i}] Não é um dict: {op_dict!r}")
            continue

        op = op_dict.get("op")
        if not op:
            errors.append(f"[op #{i}] Chave 'op' em falta.")
            continue
        if op not in VALID_OPS:
            errors.append(f"[op #{i}] Operação desconhecida '{op}'.")
            continue

        # Workspace passado como Object
        if op == "ADD_OBJECT":
            obj_payload = op_dict.get("object") or {}
            if not isinstance(obj_payload, dict):
                errors.append(f"[op #{i}] ADD_OBJECT.object deve ser um dict.")
                continue
            if "objects" in obj_payload and "fields" not in obj_payload:
                errors.append(
                    f"[op #{i}] ADD_OBJECT parece um Workspace (tem 'objects', não tem 'fields'). Recusado."
                )
            if "from" in obj_payload or "to" in obj_payload:
                errors.append(f"[op #{i}] ADD_OBJECT parece uma Relação. Recusado.")

        # Object passado como Workspace
        if op == "ADD_WORKSPACE":
            ws_payload = op_dict.get("workspace") or {}
            if not isinstance(ws_payload, dict):
                errors.append(f"[op #{i}] ADD_WORKSPACE.workspace deve ser um dict.")
                continue
            if "fields" in ws_payload:
                errors.append(
                    f"[op #{i}] ADD_WORKSPACE tem 'fields' — parece um Object. Recusado."
                )

        # Campos obrigatórios por operação
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
        if op in required:
            for key in required[op]:
                if key not in op_dict:
                    errors.append(f"[op #{i}] '{op}' falta a chave obrigatória '{key}'.")

        # ADD_FIELD: field não pode ser lista
        if op == "ADD_FIELD":
            field_payload = op_dict.get("field")
            if isinstance(field_payload, list):
                errors.append(
                    f"[op #{i}] ADD_FIELD.field é uma lista em vez de um dict único. "
                    "Usa múltiplas operações ADD_FIELD."
                )

        # ADD_RELATION: chaves 'from' e 'to' dentro do payload da relação
        if op == "ADD_RELATION":
            rel_payload = op_dict.get("relation") or {}
            if isinstance(rel_payload, dict):
                if "from" not in rel_payload or "to" not in rel_payload:
                    errors.append(
                        f"[op #{i}] ADD_RELATION.relation deve ter 'from' e 'to'."
                    )

    return len(errors) == 0, errors


# ---------------------------------------------------------------------------
# HELPERS de mutação — CORRIGIDOS para usar from_obj/to_obj (models.py)
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
    name_lower = object_name.lower()
    for ws in bp.workspaces:
        ws.objects = [o for o in ws.objects if o.lower() != name_lower]


def _rename_object_in_workspaces(bp: BlueprintModel, old_name: str, new_name: str) -> None:
    old_lower = old_name.lower()
    for ws in bp.workspaces:
        ws.objects = [new_name if o.lower() == old_lower else o for o in ws.objects]
        if ws.primary_entity.lower() == old_lower:
            ws.primary_entity = new_name


def _rename_object_in_relations(bp: BlueprintModel, old_name: str, new_name: str) -> None:
    """
    FIX: RelationModel usa from_obj e to_obj (não from_object/to_object).
    """
    old_lower = old_name.lower()
    for rel in bp.relations:
        if rel.from_obj.lower() == old_lower:
            rel.from_obj = new_name
        if rel.to_obj.lower() == old_lower:
            rel.to_obj = new_name


def _remove_object_from_relations(bp: BlueprintModel, object_name: str) -> None:
    """
    FIX: usa from_obj/to_obj (não from_object/to_object).
    """
    name_lower = object_name.lower()
    bp.relations = [
        r for r in bp.relations
        if r.from_obj.lower() != name_lower and r.to_obj.lower() != name_lower
    ]


# ---------------------------------------------------------------------------
# APLICAÇÃO DE OPERAÇÕES
# ---------------------------------------------------------------------------

def _apply_operation(bp: BlueprintModel, op_dict: Dict[str, Any]) -> List[str]:
    """
    Aplica UMA operação ao BlueprintModel (mutação in-place).
    Retorna lista de warnings não-fatais.
    """
    op = op_dict["op"]
    warnings: List[str] = []

    if op == "ADD_OBJECT":
        payload = op_dict.get("object") or {}
        try:
            new_obj = ObjectModel.model_validate(payload) if payload else ObjectModel(name="unnamed")
        except Exception as e:
            warnings.append(f"ADD_OBJECT: payload inválido — {e}")
            return warnings

        if _find_object(bp, new_obj.name):
            warnings.append(f"ADD_OBJECT: '{new_obj.name}' já existe. Ignorado.")
        else:
            bp.objects.append(new_obj)

    elif op == "REMOVE_OBJECT":
        name = op_dict["name"]
        original = len(bp.objects)
        name_lower = name.lower()
        bp.objects = [o for o in bp.objects if o.name.lower() != name_lower]
        if len(bp.objects) == original:
            warnings.append(f"REMOVE_OBJECT: '{name}' não encontrado.")
        else:
            _remove_object_from_all_workspaces(bp, name)
            _remove_object_from_relations(bp, name)

    elif op == "RENAME_OBJECT":
        old_name = op_dict["old_name"]
        new_name = op_dict["new_name"]
        obj = _find_object(bp, old_name)
        if obj is None:
            warnings.append(f"RENAME_OBJECT: '{old_name}' não encontrado.")
        elif _find_object(bp, new_name):
            warnings.append(f"RENAME_OBJECT: '{new_name}' já existe. Ignorado.")
        else:
            obj.name = new_name
            # Propagar renomeação — Caso 2 do Golden Dataset
            _rename_object_in_workspaces(bp, old_name, new_name)
            _rename_object_in_relations(bp, old_name, new_name)

    elif op == "ADD_FIELD":
        obj_name      = op_dict["object"]
        field_payload = op_dict["field"]
        obj = _find_object(bp, obj_name)

        if obj is None:
            warnings.append(f"ADD_FIELD: objeto '{obj_name}' não encontrado.")
        else:
            try:
                new_field = (
                    FieldModel.model_validate(field_payload)
                    if isinstance(field_payload, dict)
                    else FieldModel(name=str(field_payload))
                )
            except Exception as e:
                warnings.append(f"ADD_FIELD: field inválido — {e}")
                return warnings

            existing = {f.name.lower() for f in obj.fields}
            if new_field.name.lower() in existing:
                warnings.append(f"ADD_FIELD: '{new_field.name}' já existe em '{obj_name}'. Ignorado.")
            else:
                obj.fields.append(new_field)

    elif op == "REMOVE_FIELD":
        obj_name   = op_dict["object"]
        field_name = op_dict["field_name"]
        obj = _find_object(bp, obj_name)
        if obj is None:
            warnings.append(f"REMOVE_FIELD: objeto '{obj_name}' não encontrado.")
        else:
            fn_lower = field_name.lower()
            original = len(obj.fields)
            obj.fields = [f for f in obj.fields if f.name.lower() != fn_lower]
            if len(obj.fields) == original:
                warnings.append(f"REMOVE_FIELD: campo '{field_name}' não encontrado em '{obj_name}'.")

    elif op == "RENAME_FIELD":
        obj_name = op_dict["object"]
        old_name = op_dict["old_name"]
        new_name = op_dict["new_name"]
        obj = _find_object(bp, obj_name)
        if obj is None:
            warnings.append(f"RENAME_FIELD: objeto '{obj_name}' não encontrado.")
        else:
            target = next((f for f in obj.fields if f.name.lower() == old_name.lower()), None)
            if target is None:
                warnings.append(f"RENAME_FIELD: campo '{old_name}' não encontrado em '{obj_name}'.")
            elif any(f.name.lower() == new_name.lower() and f is not target for f in obj.fields):
                warnings.append(f"RENAME_FIELD: '{new_name}' já existe em '{obj_name}'. Ignorado.")
            else:
                target.name = new_name

    elif op == "RETYPE_FIELD":
        obj_name   = op_dict["object"]
        field_name = op_dict["field_name"]
        new_type   = op_dict["new_type"]
        obj = _find_object(bp, obj_name)
        if obj is None:
            warnings.append(f"RETYPE_FIELD: objeto '{obj_name}' não encontrado.")
        else:
            target = next((f for f in obj.fields if f.name.lower() == field_name.lower()), None)
            if target is None:
                warnings.append(f"RETYPE_FIELD: campo '{field_name}' não encontrado em '{obj_name}'.")
            else:
                target.type = str(new_type)

    elif op == "ADD_RELATION":
        rel_payload = op_dict.get("relation") or {}
        if not isinstance(rel_payload, dict):
            warnings.append("ADD_RELATION: payload deve ser um dict.")
            return warnings

        # FIX: RelationModel usa alias 'from'/'to' (não 'from_object'/'to_object')
        try:
            new_rel = RelationModel.model_validate(rel_payload)
        except Exception as e:
            warnings.append(f"ADD_RELATION: payload inválido — {e}")
            return warnings

        if not _find_object(bp, new_rel.from_obj):
            warnings.append(f"ADD_RELATION: from '{new_rel.from_obj}' não encontrado.")
        elif not _find_object(bp, new_rel.to_obj):
            warnings.append(f"ADD_RELATION: to '{new_rel.to_obj}' não encontrado.")
        else:
            # FIX: RelationModel não tem campo 'name' — comparar por from/to
            already_exists = any(
                r.from_obj.lower() == new_rel.from_obj.lower()
                and r.to_obj.lower() == new_rel.to_obj.lower()
                for r in bp.relations
            )
            if already_exists:
                warnings.append(
                    f"ADD_RELATION: relação '{new_rel.from_obj}→{new_rel.to_obj}' já existe. Ignorado."
                )
            else:
                bp.relations.append(new_rel)

    elif op == "REMOVE_RELATION":
        # FIX: RelationModel não tem 'name' — interpretar "name" como "from→to"
        name = op_dict["name"]
        original = len(bp.relations)
        # Suporte a formato "FromObj→ToObj" ou "FromObj_ToObj"
        parts = re.split(r"[→_\-]", name, maxsplit=1)
        if len(parts) == 2:
            from_lower, to_lower = parts[0].strip().lower(), parts[1].strip().lower()
            bp.relations = [
                r for r in bp.relations
                if not (r.from_obj.lower() == from_lower and r.to_obj.lower() == to_lower)
            ]
        else:
            # fallback: tentar match em from_obj
            bp.relations = [r for r in bp.relations if r.from_obj.lower() != name.lower()]

        if len(bp.relations) == original:
            warnings.append(f"REMOVE_RELATION: relação '{name}' não encontrada.")

    elif op == "ADD_WORKSPACE":
        payload = op_dict.get("workspace") or {}
        try:
            new_ws = WorkspaceModel.model_validate(payload) if payload else WorkspaceModel(name="unnamed_ws")
        except Exception as e:
            warnings.append(f"ADD_WORKSPACE: payload inválido — {e}")
            return warnings

        if _find_workspace(bp, new_ws.name):
            warnings.append(f"ADD_WORKSPACE: '{new_ws.name}' já existe. Ignorado.")
        else:
            valid_names = {o.name.lower() for o in bp.objects}
            original_refs = new_ws.objects[:]
            new_ws.objects = [o for o in new_ws.objects if o.lower() in valid_names]
            dropped = set(original_refs) - set(new_ws.objects)
            if dropped:
                warnings.append(f"ADD_WORKSPACE: removidas refs a objetos inexistentes: {dropped}")
            bp.workspaces.append(new_ws)

    elif op == "REMOVE_WORKSPACE":
        name = op_dict["name"]
        original = len(bp.workspaces)
        name_lower = name.lower()
        bp.workspaces = [w for w in bp.workspaces if w.name.lower() != name_lower]
        if len(bp.workspaces) == original:
            warnings.append(f"REMOVE_WORKSPACE: '{name}' não encontrado.")

    elif op == "ADD_TO_WORKSPACE":
        ws_name  = op_dict["workspace"]
        obj_name = op_dict["object"]
        ws  = _find_workspace(bp, ws_name)
        obj = _find_object(bp, obj_name)

        if ws is None:
            warnings.append(f"ADD_TO_WORKSPACE: workspace '{ws_name}' não encontrado.")
        elif obj is None:
            warnings.append(f"ADD_TO_WORKSPACE: objeto '{obj_name}' não encontrado.")
        else:
            if obj.name not in ws.objects:
                ws.objects.append(obj.name)

    elif op == "REMOVE_FROM_WORKSPACE":
        ws_name  = op_dict["workspace"]
        obj_name = op_dict["object"]
        ws = _find_workspace(bp, ws_name)
        if ws is None:
            warnings.append(f"REMOVE_FROM_WORKSPACE: workspace '{ws_name}' não encontrado.")
        else:
            original = len(ws.objects)
            ws.objects = [o for o in ws.objects if o.lower() != obj_name.lower()]
            if len(ws.objects) == original:
                warnings.append(
                    f"REMOVE_FROM_WORKSPACE: '{obj_name}' não encontrado em '{ws_name}'."
                )

    else:
        warnings.append(f"Operação desconhecida '{op}' — ignorada.")

    return warnings


# ---------------------------------------------------------------------------
# FEEDBACK LOOP — re-envio automático ao Ollama com contexto de erro
# ---------------------------------------------------------------------------

MAX_FEEDBACK_ITERATIONS = 2


def _run_with_feedback_loop(
    user_prompt: str,
    current_schema_dict: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """
    Tenta obter operações válidas do Ollama.
    Se a Firewall detetar erros, reenvia automaticamente com o contexto do erro
    (até MAX_FEEDBACK_ITERATIONS tentativas).

    Retorna (operations, all_firewall_errors).
    """
    error_context: Optional[str] = None
    operations: List[Dict[str, Any]] = []
    all_errors: List[str] = []

    for iteration in range(1, MAX_FEEDBACK_ITERATIONS + 2):
        logger.info("Iteração %d/%d do feedback loop", iteration, MAX_FEEDBACK_ITERATIONS + 1)

        prompt_text = _build_ollama_prompt(user_prompt, current_schema_dict, error_context)

        try:
            raw_response = _call_ollama(prompt_text)
        except RuntimeError as e:
            return [], [str(e)]

        try:
            diff = _parse_llm_json(raw_response)
        except ValueError as e:
            error_context = str(e)
            all_errors.append(error_context)
            logger.warning("Iteração %d: falha de parse — %s", iteration, error_context)
            continue

        # Extrair lista de operações
        ops = diff.get("operations")
        if ops is None and "op" in diff:
            ops = [diff]
        elif ops is None:
            error_context = (
                "O JSON devolvido não tem a chave 'operations'. "
                f"JSON recebido: {json.dumps(diff)[:200]}"
            )
            all_errors.append(error_context)
            logger.warning("Iteração %d: chave 'operations' em falta", iteration)
            continue

        ok, firewall_errors = _validate_diff(ops)

        if ok:
            logger.info("Iteração %d: operações válidas. Firewall passou.", iteration)
            return ops, []

        # Preparar contexto de erro para a próxima iteração
        error_context = "\n".join(firewall_errors)
        all_errors.extend(firewall_errors)
        operations = ops  # guardar para debug mesmo que inválidas
        logger.warning(
            "Iteração %d: Firewall bloqueou %d operações: %s",
            iteration, len(firewall_errors), firewall_errors,
        )

    # Esgotamos as iterações — devolver as últimas operações mesmo que imperfeitas
    # (o handler principal decidirá o que fazer em modo não-strict)
    return operations, all_errors


# ---------------------------------------------------------------------------
# GOLDEN DATASET — Testes de Regressão Inline
# ---------------------------------------------------------------------------

def run_regression_tests() -> Dict[str, Any]:
    """
    Executa os 3 casos do Golden Dataset sem chamar o Ollama.
    Testa diretamente a lógica de mutação e a Firewall.

    Uso:
        from handlers.update_schema_handler import run_regression_tests
        results = run_regression_tests()
        print(results)
    """
    results = {}

    # Schema mínimo de teste
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
            }
        ],
        "actions": [],
    }

    # ── CASO 1: Formatação — ADD_FIELD com tipo float ────────────────────────
    ops_case1 = [
        {"op": "ADD_FIELD", "object": "produto", "field": {"name": "preco", "type": "float"}}
    ]
    ok1, errs1 = _validate_diff(ops_case1)
    bp1 = parse_blueprint(base_schema)
    warns1 = _apply_operation(bp1, ops_case1[0])
    produto = _find_object(bp1, "Produto")
    campo_preco = next((f for f in (produto.fields if produto else []) if f.name == "preco"), None)

    results["caso_1_formatacao"] = {
        "descricao": "ADD_FIELD preco:float ao produto",
        "firewall_ok": ok1,
        "campo_criado": campo_preco.name if campo_preco else None,
        "tipo_correto": campo_preco.type == "float" if campo_preco else False,
        "warnings": warns1,
        "passou": bool(ok1 and campo_preco and campo_preco.type == "float"),
    }

    # ── CASO 2: Integridade — RENAME propagado para workspaces e relations ──
    ops_case2 = [
        {"op": "RENAME_OBJECT", "old_name": "cliente", "new_name": "utilizador"}
    ]
    ok2, errs2 = _validate_diff(ops_case2)
    bp2 = parse_blueprint(base_schema)
    warns2 = _apply_operation(bp2, ops_case2[0])

    # Verificar propagação
    ws_objects_after = bp2.workspaces[0].objects if bp2.workspaces else []
    relation_from_after = bp2.relations[0].from_obj if bp2.relations else ""
    obj_names_after = [o.name for o in bp2.objects]

    results["caso_2_integridade"] = {
        "descricao": "RENAME_OBJECT cliente→utilizador com propagação",
        "firewall_ok": ok2,
        "objeto_renomeado": "Utilizador" in obj_names_after or "utilizador" in obj_names_after,
        "workspace_atualizado": "utilizador" in [o.lower() for o in ws_objects_after],
        "relation_atualizada": relation_from_after.lower() == "utilizador",
        "warnings": warns2,
        "passou": bool(
            ok2
            and ("Utilizador" in obj_names_after or "utilizador" in obj_names_after)
            and "utilizador" in [o.lower() for o in ws_objects_after]
            and relation_from_after.lower() == "utilizador"
        ),
    }

    # ── CASO 3: Workspace Inexistente — deve retornar erro estruturado ──────
    ops_case3 = [
        {"op": "ADD_TO_WORKSPACE", "workspace": "WorkspaceInexistente", "object": "produto"}
    ]
    ok3, errs3 = _validate_diff(ops_case3)
    bp3 = parse_blueprint(base_schema)
    warns3 = _apply_operation(bp3, ops_case3[0])

    # ADD_TO_WORKSPACE para workspace inexistente deve gerar um warning (não crash)
    ws_inexistente_warning = any("não encontrado" in w for w in warns3)

    results["caso_3_workspace_inexistente"] = {
        "descricao": "ADD_TO_WORKSPACE para workspace que não existe",
        "firewall_ok": ok3,  # A Firewall não pode saber se o workspace existe sem o schema
        "warning_gerado": ws_inexistente_warning,
        "schema_intacto": len(bp3.workspaces) == 1,  # Não deve ter criado nada
        "warnings": warns3,
        "passou": bool(ws_inexistente_warning and len(bp3.workspaces) == 1),
    }

    # Sumário
    total = len(results)
    passed = sum(1 for r in results.values() if r.get("passou"))
    results["_sumario"] = {
        "total": total,
        "passou": passed,
        "falhou": total - passed,
        "taxa_sucesso": f"{passed}/{total}",
    }

    return results


# ---------------------------------------------------------------------------
# HANDLER PRINCIPAL
# ---------------------------------------------------------------------------

def handle_update_schema(
    prompt: str,
    current_schema: Dict[str, Any],
    *,
    strict: bool = False,  # False por defeito: aplica ops válidas, descarta inválidas
) -> Dict[str, Any]:
    """
    Entry point do handler de atualização de schema.

    Parâmetros
    ----------
    prompt : str
        Instrução do utilizador (ex: "Adiciona o campo email ao cliente").
    current_schema : dict
        Schema atual da cache — será blindado via parse_blueprint antes de qualquer operação.
    strict : bool
        Se True, qualquer erro da Firewall aborta TODAS as mutações.
        Se False (padrão), aplica as ops válidas e descarta as inválidas com warnings.

    Retorno compatível com o frontend e com main.py:
        "success", "type", "data", "mutations_applied", "mutation_log",
        "struct_warnings", "errors"
    """

    # ── 1. Blindar o schema antes de qualquer operação ───────────────────────
    bp: BlueprintModel = parse_blueprint(
        current_schema if isinstance(current_schema, dict) else {}
    )

    logger.info(
        "prompt='%s' | objects=%d | relations=%d | workspaces=%d",
        prompt, len(bp.objects), len(bp.relations), len(bp.workspaces),
    )

    # ── 2. Obter operações via Ollama + Feedback Loop ─────────────────────────
    operations, firewall_errors = _run_with_feedback_loop(prompt, bp.to_dict())

    if not operations and firewall_errors:
        logger.error("Feedback loop esgotado. Erros: %s", firewall_errors)
        return _error_response(bp, firewall_errors)

    # ── 3. Validação final (após feedback loop) ──────────────────────────────
    ok, remaining_errors = _validate_diff(operations)

    if not ok:
        if strict:
            logger.warning("Strict=True — abortando todas as mutações. Erros: %s", remaining_errors)
            return _error_response(bp, remaining_errors)
        else:
            # Filtrar para aplicar apenas as válidas
            clean_ops = []
            for op_dict in operations:
                single_ok, _ = _validate_diff([op_dict])
                if single_ok:
                    clean_ops.append(op_dict)
            logger.warning(
                "Strict=False — %d/%d ops válidas após filtragem",
                len(clean_ops), len(operations),
            )
            operations = clean_ops

    # ── 4. Deep copy defensivo ───────────────────────────────────────────────
    bp_copy: BlueprintModel = parse_blueprint(bp.to_dict())

    applied: List[str] = []
    all_warnings: List[str] = []

    # ── 5. Aplicar operações uma a uma ──────────────────────────────────────
    for op_dict in operations:
        op_label = op_dict.get("op", "UNKNOWN")
        try:
            op_warnings = _apply_operation(bp_copy, op_dict)
            all_warnings.extend(op_warnings)
            applied.append(op_label)
            logger.info("Aplicado: %s", op_label)
        except Exception as exc:
            msg = f"[{op_label}] Erro inesperado: {exc}"
            logger.exception(msg)
            all_warnings.append(msg)
            if strict:
                return _error_response(bp, [msg])

    # ── 6. Blindar saída final ───────────────────────────────────────────────
    final_bp = parse_blueprint(bp_copy.to_dict())

    logger.info(
        "Concluído — %d ops aplicadas | objects=%d | relations=%d | workspaces=%d",
        len(applied), len(final_bp.objects), len(final_bp.relations), len(final_bp.workspaces),
    )

    return {
        "success": True,
        "type": "SYSTEM",
        "schema": final_bp.to_dict(),
        "data": final_bp.to_dict(),       # alias para compatibilidade com frontend
        "mutations_applied": len(applied),
        "mutation_log": applied,
        "struct_warnings": all_warnings,
        "errors": remaining_errors if not ok else [],
    }


# ---------------------------------------------------------------------------
# HELPERS internos
# ---------------------------------------------------------------------------

def _error_response(bp: BlueprintModel, errors: List[str]) -> Dict[str, Any]:
    """Devolve o schema ORIGINAL intacto em caso de erro."""
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


# ---------------------------------------------------------------------------
# CLI — teste rápido sem servidor
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("\n🧪 AiBizCore — Testes de Regressão (Golden Dataset)\n")
    results = run_regression_tests()

    for name, result in results.items():
        if name == "_sumario":
            continue
        status = "✅ PASSOU" if result.get("passou") else "❌ FALHOU"
        print(f"{status} | {name}: {result['descricao']}")
        if not result.get("passou"):
            print(f"   Detalhes: {result}")

    sumario = results["_sumario"]
    print(f"\n📊 Sumário: {sumario['taxa_sucesso']} testes passaram\n")
