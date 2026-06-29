# ===== generator_workspaces.py =====

import json
import requests
import re

from config import MODELS, OPTIONS, OLLAMA_URL

SYSTEM_PROMPT = """
You are a senior product and UX architect.
Your task is to design access profiles (workspaces) for a system based on the provided PLAN.

CRITICAL RULES:
1. READ THE PLAN: You MUST extract and create the EXACT profiles requested by the user in the plan. DO NOT invent names.
2. DISTRIBUTE ENTITIES: Give each profile access ONLY to the objects (tables) that make sense for their role.
3. LANGUAGE: All text, names, and descriptions MUST be in European Portuguese (pt-PT).
4. REQUIRED ADMIN: ALWAYS create a workspace called "Administração" that has access to ALL entities.

OUTPUT FORMAT (ONLY VALID JSON):
{
  "workspaces": [
    {
      "name": "Nome do Perfil",
      "description": "Descrição clara do que este perfil faz",
      "icon": "user",
      "color": "#3B82F6",
      "objects": ["Tabela1", "Tabela2"],
      "primary_entity": "Tabela1",
      "permissions": ["VER", "CRIAR", "EDITAR", "APAGAR"]
    }
  ]
}
"""

def _extract_json(text: str):
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group())
    except:
        return None

def generate_workspaces(plan: dict, objects_data: dict = None):
    print("[generator_workspaces] A iniciar geração inteligente de workspaces (IA)...")
    
    entities = []
    if objects_data and objects_data.get("objects"):
        entities = [{"name": o["name"]} for o in objects_data["objects"]]
    else:
        entities = plan.get("entities", [])

    if not entities:
        return {"workspaces": []}

    all_entity_names = [e["name"] for e in entities]

    payload = {
        "model": MODELS.get("generator_workspaces", MODELS.get("generator_objects", "llama3.2:3b")),
        "prompt": f"{SYSTEM_PROMPT}\n\nPLAN:\n{json.dumps(plan, ensure_ascii=False)}\n\nAVAILABLE ENTITIES:\n{json.dumps(all_entity_names, ensure_ascii=False)}",
        "format": "json",
        "stream": False,
        "options": {**OPTIONS, "num_predict": 1024, "temperature": 0.1}
    }

    try:
        response = requests.post(OLLAMA_URL, json=payload, timeout=120)
        response.raise_for_status()
        raw = response.json().get("response", "")
        
        data = _extract_json(raw) or {"workspaces": []}
        workspaces = data.get("workspaces", [])

        # ==========================================
        # REVISÃO DE SEGURANÇA 
        # ==========================================
        has_admin = False
        
        for ws in workspaces:
            
            if not ws.get("primary_entity") and ws.get("objects"):
                ws["primary_entity"] = ws["objects"][0]
                
           
            if ws["name"].lower() in ["administração", "admin", "administrador"]:
                ws["name"] = "Administração"
                ws["objects"] = all_entity_names
                if all_entity_names:
                    ws["primary_entity"] = all_entity_names[0]
                has_admin = True

        if not has_admin:
             workspaces.append({
                "name": "Administração",
                "description": "Controlo total sobre o sistema (Gerado automaticamente)",
                "icon": "settings",
                "color": "#6B7280",
                "objects": all_entity_names,
                "primary_entity": all_entity_names[0] if all_entity_names else "",
                "permissions": ["VER", "CRIAR", "EDITAR", "APAGAR"]
            })

        print(f"[generator_workspaces] {len(workspaces)} workspaces gerados com sucesso pela IA.")
        return {"workspaces": workspaces}

    except Exception as e:
        print(f"[generator_workspaces] Erro LLM: {e}")
      
        return {"workspaces": [{
            "name": "Administração",
            "description": "Controlo total",
            "icon": "settings",
            "color": "#6B7280",
            "objects": all_entity_names,
            "primary_entity": all_entity_names[0] if all_entity_names else "",
            "permissions": ["VER", "CRIAR", "EDITAR", "APAGAR"]
        }]}
