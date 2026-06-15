# ===== handlers/create_object_handler.py =====

import time
import requests
import json
from config import MODELS, OPTIONS, OLLAMA_URL

def handle_create_object(prompt: str):
    print(f"[create_object] A gerar objeto rápido para: '{prompt}'...")
    start = time.time()

    system_prompt = """
    És um arquiteto de base de dados. O utilizador quer criar APENAS UMA entidade/tabela.
    Devolve APENAS um JSON válido com esta estrutura exata, sem blocos de markdown (```json) e sem texto extra:
    {
        "name": "NomeDoObjeto",
        "fields": [
            {"name": "id", "type": "integer"},
            {"name": "campo1", "type": "string"}
        ]
    }
    Tipos permitidos: string, integer, float, boolean, date, datetime, text.
    """

    payload = {
        "model": MODELS["generator_objects"],
        "prompt": f"{system_prompt}\n\nPedido do utilizador: {prompt}",
        "stream": False,
        "options": OPTIONS
    }

    try:
        res = requests.post(OLLAMA_URL, json=payload, timeout=30)
        res.raise_for_status()
        raw = res.json().get("response", "").strip()
        
        # Limpar o texto caso o LLM teimosamente devolva markdown
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        
        obj_data = json.loads(raw.strip())
        total_time = round(time.time() - start, 2)

        return {
            "success": True,
            "type": "OBJECT",
            "execution_time": total_time,
            "data": obj_data
        }
    except Exception as e:
        return {"success": False, "error": f"Ocorreu um erro ao gerar o objeto: {e}"}
