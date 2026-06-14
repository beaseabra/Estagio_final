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
# MAIN PIPELINE
# =========================

def run_pipeline(prompt: str, current_schema: Optional[Dict[str, Any]] = None):

    # ==========================================
    # ATALHO PARA LER A MEMÓRIA GUARDADA
    # ==========================================
    prompt_lower = prompt.lower()
    if "estado atual" in prompt_lower or ("mostra" in prompt_lower and "sistema" in prompt_lower):
        cache_path = "database/cache.json"
        if os.path.exists(cache_path):
            with open(cache_path, "r", encoding="utf-8") as f:
                saved_schema = json.load(f)
            print(f"[main] Sistema carregado com sucesso da memória ({cache_path})!")
            return {
                "success": True,
                "type": "SYSTEM",
                "data": saved_schema
            }
        else:
            return "Ainda não guardaste nenhum sistema na memória (ficheiro database/cache.json não encontrado)."
    
    # ==========================================
    # 🔥 BLINDAGEM DE SINGLE SOURCE OF TRUTH 🔥
    # O Python ignora o frontend e vai ler SEMPRE o ficheiro
    # atualizado pelo teu "Botão Azul" (database/cache.json).
    # Isto resolve de vez o problema do "Cérebro Dividido".
    # ==========================================
    cache_path = "database/cache.json"
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                disk_schema = json.load(f)
                # Se o ficheiro tiver dados reais, sobrepõe a memória temporária!
                if isinstance(disk_schema, dict) and ("objects" in disk_schema or "workspaces" in disk_schema):
                    current_schema = disk_schema
                    print("[main] 🛡️ current_schema sincronizado com a Fonte da Verdade (database/cache.json)!")
        except Exception as e:
            print(f"[main] Erro ao sincronizar cache: {e}")
    # ==========================================

    # CORREÇÃO FASE 1: Garantir que o schema nunca é None se a cache estiver vazia
    # Evita falsos positivos no router e protege os handlers
    if current_schema is None:
        current_schema = {"objects": [], "workspaces": [], "actions": []}

    # CORREÇÃO: Limpar a poluição de contexto do frontend.
    # Extraímos estritamente o pedido atual para não confundir o motor.
    actual_prompt = prompt
    if "Pedido atual (responde a isto):" in prompt:
        actual_prompt = prompt.split("Pedido atual (responde a isto):")[-1].strip()
    elif "Pedido atual" in prompt:
        actual_prompt = prompt.split("Pedido atual")[-1].split(":")[-1].strip()

    # Passamos apenas o texto limpo para o classificador e para os handlers
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
    
    # Executar a modificação ou criação utilizando a instrução isolada
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