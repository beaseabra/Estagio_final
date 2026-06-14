# ===== handlers/update_schema_handler.py =====

import json
import requests
import unicodedata
from typing import Dict, Any, Union

from config import MODELS, OPTIONS, OLLAMA_URL

def normalize_string(s: str) -> str:
    if not s: return ""
    # Remove acentos e converte para minúsculas, retirando espaços extra
    s = ''.join(c for c in unicodedata.normalize('NFD', s) if unicodedata.category(c) != 'Mn')
    return s.lower().strip()

def names_match(target_name: str, existing_name: str) -> bool:
    if not target_name or not existing_name: return False
    n1 = normalize_string(target_name)
    n2 = normalize_string(existing_name)
    if n1 == n2: return True
    if n1 and n2 and (n1 in n2 or n2 in n1):
        return True
    return False

def handle_update_schema(prompt: str, current_schema: Dict[str, Any]) -> Union[Dict[str, Any], str]:
    if not current_schema or not isinstance(current_schema, dict):
        current_schema = {"objects": [], "workspaces": [], "actions": []}
        
    current_schema.setdefault("objects", [])
    current_schema.setdefault("workspaces", [])
    current_schema.setdefault("actions", [])

    tables_context = []
    for obj in current_schema.get("objects", []):
        fields = [f.get("name") for f in obj.get("fields", []) if isinstance(f, dict)]
        tables_context.append(f"'{obj.get('name')}' (Campos atuais: {', '.join(fields)})")

    actions_context = [a.get("name") for a in current_schema.get("actions", []) if isinstance(a, dict)]
    ws_context = [ws.get("name") for ws in current_schema.get("workspaces", []) if isinstance(ws, dict)]

    tables_str = " | ".join(tables_context) if tables_context else "Nenhuma"
    actions_str = ", ".join(actions_context) if actions_context else "Nenhuma"
    ws_str = ", ".join(ws_context) if ws_context else "Nenhum"

    system_prompt = f"""You are a strict JSON API modifying a software architecture.

CURRENT STATE MAP:
- WORKSPACES: {ws_str}
- TABLES: {tables_str}
- ACTIONS: {actions_str}

CRITICAL RULES:
1. Only output elements being modified, added, or deleted.
2. To RENAME a table, field, or action, use "original_name" with the exact current name, and "name" with the new name.
3. To CHANGE A TYPE, provide the new "type".
4. To DELETE, add "delete": true.

FEW-SHOT EXAMPLE:
User: "Na tabela Produto, altera preco para string e renomeia qtd para quantidade. Renomeia a ação Vender para Vender Produto."
Output:
{{
  "objects": [
    {{
      "original_name": "Produto",
      "name": "Produto",
      "fields": [
        {{ "original_name": "preco", "name": "preco", "type": "string" }},
        {{ "original_name": "qtd", "name": "quantidade", "type": "string" }}
      ]
    }}
  ],
  "actions": [
    {{ "original_name": "Vender", "name": "Vender Produto", "type": "DOMAIN_ACTION" }}
  ]
}}

STRICT JSON ONLY. No markdown.
"""

    payload = {
        "model": MODELS.get("system", "llama3.2:3b"),
        "prompt": system_prompt + "\n\nUSER REQUEST: " + prompt,
        "format": "json",
        "stream": False,
        "options": {**OPTIONS, "temperature": 0.0, "num_predict": 2048}
    }

    try:
        response = requests.post(OLLAMA_URL, json=payload, timeout=120)
        response.raise_for_status()
        raw_text = response.json().get("response", "").strip()
        
        # O NOSSO OLHEIRO: Agora vais ver no terminal o que a IA pensou!
        print(f"\n[update_schema] 🧠 Resposta bruta do LLM:\n{raw_text}\n")
        
        try:
            new_elements = json.loads(raw_text)
        except json.JSONDecodeError:
            return "Não consegui interpretar a estrutura. Por favor, detalhe mais a alteração."
        
        # 1. PROCESSAR WORKSPACES
        for item in new_elements.get("workspaces", []):
            ws_name = item.get("name")
            ws_orig = item.get("original_name", ws_name)
            if not ws_name: continue
            
            updated = False
            for i, existing_ws in enumerate(current_schema["workspaces"]):
                if names_match(existing_ws.get("name"), ws_orig):
                    current_schema["workspaces"][i]["name"] = ws_name
                    for k, v in item.items():
                        if k not in ["id", "_id", "actions", "original_name", "name"]:
                            current_schema["workspaces"][i][k] = v
                    updated = True
                    break
            
            if not updated and not item.get("delete"):
                clean_item = {k: v for k, v in item.items() if k not in ["actions", "original_name"]}
                current_schema["workspaces"].append(clean_item)

        # 2. PROCESSAR OBJECTS E CAMPOS
        for item in new_elements.get("objects", []):
            obj_name = item.get("name")
            obj_orig = item.get("original_name", obj_name)
            is_obj_del = item.get("delete", False)
            
            if not obj_name: continue

            if is_obj_del:
                current_schema["objects"] = [o for o in current_schema["objects"] if not names_match(o.get("name"), obj_orig)]
                continue

            updated = False
            for i, existing_obj in enumerate(current_schema["objects"]):
                if names_match(existing_obj.get("name"), obj_orig):
                    current_schema["objects"][i]["name"] = obj_name 
                    
                    existing_fields = existing_obj.get("fields", [])
                    for nf in item.get("fields", []):
                        f_name = nf.get("name")
                        f_orig = nf.get("original_name", f_name)
                        is_f_del = nf.get("delete", False)
                        
                        if not f_name: continue
                        
                        if is_f_del:
                            existing_fields = [f for f in existing_fields if normalize_string(f.get("name")) != normalize_string(f_orig)]
                            continue
                            
                        f_found = False
                        for ef in existing_fields:
                            if normalize_string(ef.get("name")) == normalize_string(f_orig):
                                ef["name"] = f_name
                                ef["type"] = nf.get("type", ef.get("type"))
                                f_found = True
                                break
                        
                        if not f_found:
                            existing_fields.append({"name": f_name, "type": nf.get("type", "string")})
                            
                    current_schema["objects"][i]["fields"] = existing_fields
                    updated = True
                    break
            
            if not updated:
                valid_fields = [{"name": f.get("name"), "type": f.get("type", "string")} for f in item.get("fields", []) if f.get("name")]
                item["fields"] = valid_fields
                clean_obj = {k: v for k, v in item.items() if k not in ["original_name", "delete"]}
                current_schema["objects"].append(clean_obj)

        # 3. PROCESSAR AÇÕES
        new_actions = new_elements.get("actions", [])
        for ws in new_elements.get("workspaces", []):
            if "actions" in ws and isinstance(ws["actions"], list):
                new_actions.extend(ws["actions"])

        for new_act in new_actions:
            act_name = new_act.get("name")
            act_orig = new_act.get("original_name", act_name)
            is_act_del = new_act.get("delete", False)
            
            if not act_name or "undefined" in act_name.lower() or "add_" in act_name.lower(): continue

            if is_act_del:
                current_schema["actions"] = [a for a in current_schema["actions"] if not names_match(a.get("name"), act_orig)]
                for ws in current_schema["workspaces"]:
                    if "permissions" in ws:
                        ws["permissions"] = [p for p in ws["permissions"] if not names_match(p, act_orig)]
                continue

            act_found = False
            new_type = new_act.get("type", "DOMAIN_ACTION")
            
            for a in current_schema["actions"]:
                if names_match(a.get("name"), act_orig):
                    a["name"] = act_name
                    a["type"] = new_type
                    act_found = True
                    if normalize_string(act_orig) != normalize_string(act_name):
                        for ws in current_schema["workspaces"]:
                            if "permissions" in ws:
                                ws["permissions"] = [act_name if names_match(p, act_orig) else p for p in ws["permissions"]]
                    break
            
            if not act_found:
                current_schema["actions"].append({"name": act_name, "type": new_type})

        return {"success": True, "type": "SYSTEM", "data": current_schema}
        
    except Exception as e:
        return {"success": False, "error": str(e)}
