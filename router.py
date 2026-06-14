# ===== router.py =====

import time
import requests
from typing import Optional, Dict, Any
from config import MODELS, OPTIONS, OLLAMA_URL

# =========================
# RULE-BASED CLASSIFICATION
# =========================

def _rule_based_classification(prompt: str, current_schema: Optional[Dict[str, Any]] = None) -> str:

    if not prompt:
        return "UNKNOWN"

    p = prompt.lower()

    # Bloqueia falsos positivos (perguntas teóricas ou interações de chat)
    chat_overrides = [
        "piada", "joke", "explica", "o que é", "qual é", "como funciona", "resume"
    ]
    if any(word in p for word in chat_overrides):
        return "CHAT"

    # Identificadores de intenção de modificação (UPDATE)
    has_update = any(
        word in p
        for word in ["adiciona", "adicionar", "muda", "mudar", "altera", "alterar", "remove", "remover", "apaga", "apagar", "corrige", "corrigir", "atualiza", "atualizar", "renomeia", "renomear"]
    )

    # Identificadores de intenção de criação do zero (CREATE)
    has_create = any(
        word in p
        for word in ["cria", "criar", "gera", "gerar", "build", "create", "faz", "desenha"]
    )

    has_system = any(
        word in p for word in ["sistema", "system", "plataforma", "software", "app", "aplicativo", "projeto", "erp", "arquitetura"]
    )
    
    has_object = any(
        word in p for word in ["objeto", "tabela", "entidade", "object", "entity", "modelo"]
    )
    
    has_workspace = any(
        word in p for word in ["workspace", "workspaces", "perfil", "perfis", "acesso", "role", "roles"]
    )

    # 1. Avaliação de Atualização (UPDATE)
    if current_schema is not None and len(current_schema.get("objects", [])) > 0:
        if has_update or (has_create and not has_system):
            return "UPDATE_SCHEMA"

    # 2. Avaliação de Criação (CREATE)
    # 🔥 CORREÇÃO: Se pede sistema, ou pede objetos + workspaces juntos, é um CREATE_SYSTEM garantido!
    if has_create or has_system:
        # Intenção isolada explícita
        if "apenas" in p or "só" in p:
            if has_workspace: return "CREATE_WORKSPACE"
            if has_object: return "CREATE_OBJECT"

        # A regra inteligente:
        if has_system or (has_workspace and has_object):
            return "CREATE_SYSTEM"
            
        if has_workspace:
            return "CREATE_WORKSPACE"
            
        if has_object:
            return "CREATE_OBJECT"
            
        return "UNKNOWN"

    # 3. Filtro rápido para conversas normais
    chat_keywords = [
        "olá", "ola", "bom dia", "boa tarde", "boa noite", "ajuda", "quem és", "tudo bem",
        "chamo"
    ]
    if any(word in p for word in chat_keywords):
        return "CHAT"

    return "UNKNOWN"


# =========================
# LLM CLASSIFICATION
# =========================

def _llm_classification(prompt: str, current_schema: Optional[Dict[str, Any]] = None) -> str:

    schema_context = "YES" if current_schema is not None else "NO"

    system_prompt = f"""
You are a request classifier for a Software Architecture tool.

CRITICAL DOMAIN RULE:
If the user asks to create, build, or design something that is NOT related to software engineering, databases, system components, or business application profiles (e.g., "cria o ceu", "cria o universo", "make a cake", "write a poem"), you MUST classify it as REJECTED.

CONTEXT:
Does the user currently have an active software architecture loaded on their screen? {schema_context}

RULES:
1. If there is an active architecture (YES) and the user asks to modify, add, remove, or update it, classify as UPDATE_SCHEMA.
2. If the user asks to create a completely new system from scratch, classify as CREATE_SYSTEM.
3. If the user asks to create only an isolated object/table, classify as CREATE_OBJECT.
4. If the user asks to create only a workspace/role, classify as CREATE_WORKSPACE.

You must output ONLY one label:
- CREATE_SYSTEM
- CREATE_OBJECT
- CREATE_WORKSPACE
- UPDATE_SCHEMA
- CHAT
- REJECTED
- UNKNOWN

Do not explain.
Do not write extra text.
"""

    payload = {
        "model": MODELS["router"],
        "prompt": f"{system_prompt}\n\nUser prompt:\n{prompt}\n\nLabel:",
        "stream": False,
        "options": OPTIONS
    }

    try:
        start_time = time.time()
        response = requests.post(
            OLLAMA_URL,
            json=payload,
            timeout=90
        )
        response.raise_for_status()
        
        duration = time.time() - start_time
        print(f"[router] Classificação LLM concluída em {duration:.2f} segundos.")

        raw = response.json().get("response", "").strip().upper()

        normalized = (
            raw.replace(".", "")
               .replace("_", "")
               .replace(" ", "")
               .strip()
        )

        if "UPDATESCHEMA" in normalized: return "UPDATE_SCHEMA"
        if "CREATEWORKSPACE" in normalized: return "CREATE_WORKSPACE"
        if "CREATEOBJECT" in normalized: return "CREATE_OBJECT"
        if "CREATESYSTEM" in normalized: return "CREATE_SYSTEM"
        if "CHAT" in normalized: return "CHAT"
        if "REJECTED" in normalized: return "REJECTED"

    except Exception as e:
        print(f"[router] erro LLM: {e}")

    return "UNKNOWN"


# =========================
# MAIN CLASSIFIER
# =========================

def classify(prompt: str, current_schema: Optional[Dict[str, Any]] = None) -> str:

    cleaned_prompt = prompt
    if "Pedido atual" in prompt:
        try:
            cleaned_prompt = prompt.split("Pedido atual")[-1].split(":")[-1].strip()
        except Exception:
            cleaned_prompt = prompt

    print(f"[router] Classificando texto real: '{cleaned_prompt}'")
    print(f"[router] Schema ativo em memoria: {'Sim' if current_schema else 'Nao'}")

    rule_result = _rule_based_classification(cleaned_prompt, current_schema)
    if rule_result != "UNKNOWN":
        print(f"[router] deterministic match ({rule_result})")
        return rule_result

    print("[router] fallback LLM")
    llm_result = _llm_classification(cleaned_prompt, current_schema)

    if llm_result == "UNKNOWN":
        return "CHAT"

    return llm_result