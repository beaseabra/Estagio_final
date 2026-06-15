# ===== router.py =====
# Refatorado — AiBizCore
#
# CORREÇÕES CRÍTICAS:
#
# BUG 1 (TESTE 2 — Adição Isolada):
#   ANTES: `len(current_schema.get("objects", [])) > 0` → se schema={objects:[]} (cache vazia
#   mas existente), len=0 → condição falha → CREATE_SYSTEM apaga tudo.
#   DEPOIS: a presença do schema é o sinal suficiente para ativar UPDATE_SCHEMA.
#   O critério de "schema ativo" é: current_schema é um dict com pelo menos UMA das
#   chaves raiz presente (objects, workspaces, actions), independentemente de estarem vazias.
#
# BUG 2 (TESTE 2 — keyword "ação"):
#   "Cria um novo objeto chamado 'Encomenda' e a ação 'Processar Encomenda'"
#   → "ação" não estava nas keywords has_create mas ativava has_object indiretamente.
#   → Adicionada keyword "ação" e "acção" ao vocabulário.
#
# BUG 3 (TESTE 3 — keyword "renomeia"):
#   "renomeia" não estava na lista has_update → fallback LLM → potencial UNKNOWN → CHAT.
#   CORRIGIDO.
#
# BUG 4 (GERAL — prompt com contexto injetado pelo frontend):
#   A limpeza do prompt antes da classificação já existia mas não era aplicada consistentemente.
#   Centralizada em _extract_actual_prompt().

from __future__ import annotations

import re
import time
from typing import Any, Dict, Optional

import requests

from config import MODELS, OPTIONS, OLLAMA_URL


# ─────────────────────────────────────────────────────────────────────────────
# UTILITÁRIOS
# ─────────────────────────────────────────────────────────────────────────────

def _extract_actual_prompt(prompt: str) -> str:
    """
    Remove o contexto de histórico injetado pelo frontend antes de classificar.
    O frontend envia: "CONTEXTO:\\n...\\nPedido atual (responde a isto): <prompt real>"
    """
    if not prompt:
        return ""
    if "Pedido atual (responde a isto):" in prompt:
        return prompt.split("Pedido atual (responde a isto):")[-1].strip()
    if "Pedido atual" in prompt:
        return prompt.split("Pedido atual")[-1].split(":")[-1].strip()
    return prompt.strip()


def _schema_is_active(current_schema: Optional[Dict[str, Any]]) -> bool:
    """
    Um schema é considerado ativo se for um dict com pelo menos uma das chaves raiz.
    Isto inclui schemas com listas VAZIAS (ex: objects=[] após init da cache).

    CRÍTICO: Não usar len(objects) > 0 — um schema com 0 objetos ainda é um
    contexto ativo e pedidos de criação devem ser tratados como UPDATE_SCHEMA
    (adição ao sistema existente), não CREATE_SYSTEM (criação do zero).
    """
    if not isinstance(current_schema, dict):
        return False
    return any(k in current_schema for k in ("objects", "workspaces", "actions"))


# ─────────────────────────────────────────────────────────────────────────────
# CLASSIFICAÇÃO DETERMINÍSTICA (rule-based)
# ─────────────────────────────────────────────────────────────────────────────

# Vocabulário de intenção — mantido centralizado para fácil manutenção

_CHAT_OVERRIDES = {
    "piada", "joke", "explica", "o que é", "qual é",
    "como funciona", "resume", "o que são",
}

_UPDATE_KEYWORDS = {
    "adiciona", "adicionar", "muda", "mudar", "altera", "alterar",
    "remove", "remover", "apaga", "apagar", "corrige", "corrigir",
    "atualiza", "atualizar", "renomeia", "renomear",       # BUG 3 fix
    "elimina", "eliminar", "edita", "editar", "modifica", "modificar",
    "acrescenta", "acrescentar",
}

_CREATE_KEYWORDS = {
    "cria", "criar", "gera", "gerar", "build", "create",
    "faz", "desenha", "faz", "novo", "nova",
}

_SYSTEM_KEYWORDS = {
    "sistema", "system", "plataforma", "software", "app",
    "aplicativo", "projeto", "erp", "arquitetura",
}

_OBJECT_KEYWORDS = {
    "objeto", "tabela", "entidade", "object", "entity", "modelo",
    "ação", "acção", "action",                              # BUG 2 fix
}

_WORKSPACE_KEYWORDS = {
    "workspace", "workspaces", "perfil", "perfis",
    "acesso", "role", "roles",
}

_CHAT_KEYWORDS = {
    "olá", "ola", "bom dia", "boa tarde", "boa noite",
    "ajuda", "quem és", "tudo bem", "chamo",
}


def _rule_based_classification(
    prompt: str,
    current_schema: Optional[Dict[str, Any]] = None,
) -> str:

    if not prompt:
        return "UNKNOWN"

    p = prompt.lower()

    # ── Forçar CHAT para perguntas teóricas ───────────────────────────────────
    if any(w in p for w in _CHAT_OVERRIDES):
        return "CHAT"

    has_update    = any(w in p for w in _UPDATE_KEYWORDS)
    has_create    = any(w in p for w in _CREATE_KEYWORDS)
    has_system    = any(w in p for w in _SYSTEM_KEYWORDS)
    has_object    = any(w in p for w in _OBJECT_KEYWORDS)
    has_workspace = any(w in p for w in _WORKSPACE_KEYWORDS)

    schema_active = _schema_is_active(current_schema)

    # ── UPDATE_SCHEMA: schema ativo + intenção de modificar ───────────────────
    # BUG 1 FIX: schema_active usa _schema_is_active(), não len() > 0
    if schema_active:
        if has_update:
            return "UPDATE_SCHEMA"
        # CREATE sem contexto de sistema completo → adicionar ao schema existente
        if has_create and not has_system:
            return "UPDATE_SCHEMA"

    # ── CREATE paths (sem schema ativo, ou criação completa de sistema) ───────
    if has_create or has_system:
        # Intenção isolada explícita
        if "apenas" in p or "só" in p:
            if has_workspace:
                return "CREATE_WORKSPACE"
            if has_object:
                return "CREATE_OBJECT"

        if has_system or (has_workspace and has_object):
            return "CREATE_SYSTEM"
        if has_workspace:
            return "CREATE_WORKSPACE"
        if has_object:
            return "CREATE_OBJECT"

        return "UNKNOWN"

    # ── CHAT de saudação ──────────────────────────────────────────────────────
    if any(w in p for w in _CHAT_KEYWORDS):
        return "CHAT"

    return "UNKNOWN"


# ─────────────────────────────────────────────────────────────────────────────
# CLASSIFICAÇÃO LLM (fallback)
# ─────────────────────────────────────────────────────────────────────────────

def _llm_classification(
    prompt: str,
    current_schema: Optional[Dict[str, Any]] = None,
) -> str:

    schema_context = "YES" if _schema_is_active(current_schema) else "NO"

    system_prompt = f"""
You are a request classifier for a Software Architecture tool.

CRITICAL DOMAIN RULE:
If the user asks to create, build, or design something unrelated to software engineering,
databases, system components, or business application profiles, classify as REJECTED.

CONTEXT:
Does the user have an active software architecture loaded? {schema_context}

RULES:
1. Active architecture (YES) + modify/add/remove/update → UPDATE_SCHEMA.
2. Create new system from scratch → CREATE_SYSTEM.
3. Create only an isolated object/table → CREATE_OBJECT.
4. Create only a workspace/role → CREATE_WORKSPACE.
5. General conversation → CHAT.

Output ONLY one label (no explanation, no extra text):
CREATE_SYSTEM | CREATE_OBJECT | CREATE_WORKSPACE | UPDATE_SCHEMA | CHAT | REJECTED | UNKNOWN
"""

    payload = {
        "model": MODELS["router"],
        "prompt": f"{system_prompt}\n\nUser prompt:\n{prompt}\n\nLabel:",
        "stream": False,
        "options": OPTIONS,
    }

    try:
        start = time.time()
        response = requests.post(OLLAMA_URL, json=payload, timeout=90)
        response.raise_for_status()
        duration = time.time() - start
        print(f"[router] LLM classificação em {duration:.2f}s")

        raw = response.json().get("response", "").strip().upper()
        normalized = re.sub(r"[\.\s_]", "", raw)

        if "UPDATESCHEMA"    in normalized: return "UPDATE_SCHEMA"
        if "CREATEWORKSPACE" in normalized: return "CREATE_WORKSPACE"
        if "CREATEOBJECT"    in normalized: return "CREATE_OBJECT"
        if "CREATESYSTEM"    in normalized: return "CREATE_SYSTEM"
        if "CHAT"            in normalized: return "CHAT"
        if "REJECTED"        in normalized: return "REJECTED"

    except Exception as e:
        print(f"[router] erro LLM: {e}")

    return "UNKNOWN"


# ─────────────────────────────────────────────────────────────────────────────
# CLASSIFICADOR PRINCIPAL
# ─────────────────────────────────────────────────────────────────────────────

def classify(
    prompt: str,
    current_schema: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Classifica o prompt e devolve a rota correta.

    Pipeline:
    1. Limpar contexto de histórico injetado pelo frontend
    2. Regras determinísticas (rápido, sem LLM)
    3. Fallback LLM se regras não forem suficientes
    4. CHAT como último resort
    """

    cleaned = _extract_actual_prompt(prompt)

    print(f"[router] prompt limpo: '{cleaned}'")
    print(f"[router] schema ativo: {'Sim' if _schema_is_active(current_schema) else 'Não'}")

    # ── Passo 1: regras determinísticas ───────────────────────────────────────
    rule_result = _rule_based_classification(cleaned, current_schema)
    if rule_result != "UNKNOWN":
        print(f"[router] deterministic → {rule_result}")
        return rule_result

    # ── Passo 2: fallback LLM ─────────────────────────────────────────────────
    print("[router] fallback LLM")
    llm_result = _llm_classification(cleaned, current_schema)

    if llm_result == "UNKNOWN":
        return "CHAT"

    return llm_result