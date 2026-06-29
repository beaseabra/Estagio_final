# ===== handlers/create_workspace_handler.py =====

import time
import requests
import json
from config import MODELS, OPTIONS, OLLAMA_URL

def handle_create_workspace(prompt: str) -> dict:
    print(f"[create_workspace] A iniciar criação para: '{prompt}'...")
    start = time.time()

    system_prompt = """
    You are an AI designing a software architecture wrapper.
    You must output a strictly valid JSON structure with three root keys: "objects", "workspaces", and "actions".
    
    CRITICAL RULES:
    1. "objects" is a list of tables with fields and types.
    2. "workspaces" is a list of access profiles. Each workspace MUST include "primary_entity" (the main focus entity string requested by the user, e.g., "Paciente").
    3. "actions" is a list of business operations requested by the user.
    
    Format example:
    {
      "objects": [{"name": "Exemplo", "fields": [{"name": "id", "type": "number"}]}],
      "workspaces": [{"name": "Perfil", "primary_entity": "Paciente", "objects": ["Exemplo"], "permissions": ["VIEW"]}],
      "actions": [{"name": "Nome da Ação", "description": "O que faz", "type": "DOMAIN_ACTION"}]
    }
    
    Output STRICTLY the JSON matching this format, nothing else.
    """
    
    payload = {
        "model": MODELS.get("generator_workspaces", "llama3.2:3b"),
        "prompt": f"{system_prompt}\n\nUSER REQUEST: {prompt}",
        "format": "json",
        "stream": False,
        "options": {**OPTIONS, "temperature": 0.0}
    }

    try:
        response = requests.post(OLLAMA_URL, json=payload, timeout=120)
        response.raise_for_status()
        raw_text = response.json().get("response", "").strip()
        
        print(f"[create_workspace] Resposta bruta da IA:\n{raw_text}")
        result = json.loads(raw_text)
        
        final_schema = {
            "objects": result.get("objects", []),
            "workspaces": result.get("workspaces", []),
            "actions": result.get("actions", [])
        }
        
        print(f"[create_workspace] Sucesso! Ações extraídas: {len(final_schema['actions'])}")
        print(f"[create_workspace] Tempo de execução: {time.time() - start:.2f}s")
        
        return {
            "success": True,
            "type": "WORKSPACE",
            "data": final_schema
        }
        
    except Exception as e:
        print(f"[create_workspace] Erro crítico no handler: {e}")
        return {"success": False, "error": str(e)}
