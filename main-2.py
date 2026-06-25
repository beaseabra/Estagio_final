# ===== main.py =====

import os
import json
from typing import Optional, Dict, Any
from router import classify

from handlers.create_system_handler import handle_create_system
from handlers.create_object_handler import handle_create_object
from handlers.create_workspace_handler import handle_create_workspace
from handlers.chat_handler import handle_chat
from handlers.update_schema_handler import handle_update_schema

# =========================
# ROUTES
# =========================

ROUTES = {
    "CREATE_SYSTEM": handle_create_system,
    "CREATE_OBJECT": handle_create_object,
    "CREATE_WORKSPACE": handle_create_workspace,
    "CHAT": handle_chat,
    "UPDATE_SCHEMA": handle_update_schema
}


# =========================
# HELPERS
# =========================

def empty_schema() -> Dict[str, Any]:
    """
    Cria sempre um schema vazio novo.

    Importante:
    - inclui relations, porque agora o blueprint completo é:
      objects + relations + workspaces + actions
    """
    return {
        "objects": [],
        "relations": [],
        "workspaces": [],
        "actions": []
    }


def normalize_schema(schema: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Aceita vários formatos possíveis vindos do frontend/API e devolve
    sempre o blueprint real.

    Formatos aceites:
    - {"objects": [...], "relations": [...], ...}
    - {"schema": {"objects": [...]}}
    - {"data": {"objects": [...]}}
    - {"system": {"objects": [...]}}
    """
    if not isinstance(schema, dict):
        return None

    if isinstance(schema.get("schema"), dict):
        schema = schema["schema"]
    elif isinstance(schema.get("data"), dict):
        schema = schema["data"]
    elif isinstance(schema.get("system"), dict):
        schema = schema["system"]

    if not isinstance(schema, dict):
        return None

    normalized = {
        "objects": schema.get("objects", []) if isinstance(schema.get("objects", []), list) else [],
        "relations": schema.get("relations", []) if isinstance(schema.get("relations", []), list) else [],
        "workspaces": schema.get("workspaces", []) if isinstance(schema.get("workspaces", []), list) else [],
        "actions": schema.get("actions", []) if isinstance(schema.get("actions", []), list) else []
    }

    # Preserva outros metadados que eventualmente existam.
    for key, value in schema.items():
        if key not in normalized:
            normalized[key] = value

    return normalized


def schema_has_content(schema: Optional[Dict[str, Any]]) -> bool:
    """
    Diz se existe estado real no schema.
    """
    if not isinstance(schema, dict):
        return False

    for key in ("objects", "relations", "workspaces", "actions"):
        value = schema.get(key)
        if isinstance(value, list) and len(value) > 0:
            return True

    return False


def load_cache_schema(cache_path: str = "database/cache.json") -> Optional[Dict[str, Any]]:
    """
    Lê database/cache.json se existir e tiver conteúdo válido.
    """
    if not os.path.exists(cache_path):
        return None

    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            disk_schema = json.load(f)

        disk_schema = normalize_schema(disk_schema)

        if schema_has_content(disk_schema):
            return disk_schema

    except Exception as e:
        print(f"[main] Erro ao sincronizar cache: {e}")

    return None


def extract_actual_prompt(prompt: str) -> str:
    """
    Remove a poluição de contexto adicionada pelo frontend e fica só
    com o pedido atual.
    """
    actual_prompt = prompt

    if "Pedido atual (responde a isto):" in prompt:
        actual_prompt = prompt.split("Pedido atual (responde a isto):")[-1].strip()
    elif "Pedido atual" in prompt:
        actual_prompt = prompt.split("Pedido atual")[-1].split(":")[-1].strip()

    return actual_prompt


# =========================
# MAIN PIPELINE
# =========================

def run_pipeline(prompt: str, current_schema: Optional[Dict[str, Any]] = None):

    # ==========================================
    # ATALHO PARA LER A MEMÓRIA GUARDADA
    # ==========================================
    prompt_lower = prompt.lower()

    if "estado atual" in prompt_lower or ("mostra" in prompt_lower and "sistema" in prompt_lower):
        cache_path = "database/cache.json"
        saved_schema = load_cache_schema(cache_path)

        if saved_schema is not None:
            print(f"[main] Sistema carregado com sucesso da memória ({cache_path})!")
            return {
                "success": True,
                "type": "SYSTEM",
                "data": saved_schema
            }

        return "Ainda não guardaste nenhum sistema na memória (ficheiro database/cache.json não encontrado ou vazio)."

    # ==========================================
    # FONTE DE VERDADE DO TURNO
    # ==========================================
    # Antes, o backend ignorava o frontend e carregava SEMPRE
    # database/cache.json. Isso fazia desaparecer objetos criados
    # no canvas mas ainda não guardados na memória.
    #
    # Agora:
    # 1. Se o frontend enviou current_schema com conteúdo, usa esse.
    # 2. Só usa database/cache.json se NÃO existir schema ativo vindo do frontend.
    # 3. Se não houver nenhum dos dois, usa schema vazio.
    # ==========================================

    current_schema = normalize_schema(current_schema)

    if schema_has_content(current_schema):
        print("[main] current_schema recebido do frontend/API e mantido como estado ativo.")
    else:
        cached_schema = load_cache_schema()
        if cached_schema is not None:
            current_schema = cached_schema
            print("[main] current_schema carregado da cache porque o frontend não enviou schema ativo.")
        else:
            current_schema = empty_schema()
            print("[main] sem current_schema ativo nem cache válida; a usar schema vazio.")

    # CORREÇÃO: Limpar a poluição de contexto do frontend.
    # Extraímos estritamente o pedido atual para não confundir o motor.
    actual_prompt = extract_actual_prompt(prompt)

    # Passamos apenas o texto limpo para o classificador e para os handlers.
    route = classify(actual_prompt, current_schema)

    print(f"\n[main] route = {route}")
    print(f"[main] prompt limpo a processar = '{actual_prompt}'")

    if route == "REJECTED":
        return "Não consigo responder a isso."

    if route not in ROUTES:
        return {
            "success": False,
            "error": f"Route desconhecida: {route}"
        }

    handler = ROUTES[route]

    # Executar a modificação ou criação utilizando a instrução isolada.
    if route == "UPDATE_SCHEMA":
        result = handler(actual_prompt, current_schema)
    else:
        result = handler(actual_prompt)

    return result


# =========================
# CLI LOOP
# =========================

if __name__ == "__main__":

    print("Assistente AiBizCore iniciado. Escreve 'sair' para terminar.\n")

    while True:
        prompt = input(">> ")

        if prompt.lower() in ["sair", "exit", "quit"]:
            print("A terminar...")
            break

        result = run_pipeline(prompt, None)

        print("\nRESULTADO:\n")

        if isinstance(result, dict):
            if "evaluation" in result:
                print("Score:", result["evaluation"].get("score"))
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            print(result)

        print("\n" + "-"*50 + "\n")
