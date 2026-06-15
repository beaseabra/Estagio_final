# ===== api.py =====

import json
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional, Dict, Any

# Importamos a função principal do pipeline
from main import run_pipeline

app = FastAPI(
    title="AiBizCore API",
    description="API do Protótipo do ecossistema de IA AiBizCore v4 com Estrutura de Memória",
    version="4.1"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Classes de suporte para os pedidos
class MessageObject(BaseModel):
    role: str 
    content: str

class PromptPayload(BaseModel):
    prompt: str
    history: Optional[List[MessageObject]] = []
    current_schema: Optional[Dict[str, Any]] = None 

class SaveCachePayload(BaseModel):
    schema_data: Dict[str, Any]

@app.get("/")
def health_check():
    return {
        "status": "online",
        "message": "API do AiBizCore a funcionar perfeitamente!"
    }

# ROTA PARA LER A MEMÓRIA (Obrigatório para o frontend ver as ações)
@app.get("/api/get_schema")
def get_schema():
    try:
        with open("database/cache.json", "r", encoding="utf-8") as f:
            data = json.load(f)
            return {
                "objects": data.get("objects", []),
                "workspaces": data.get("workspaces", []),
                "actions": data.get("actions", []) # <--- Garantia de envio das ações
            }
    except FileNotFoundError:
        # Se o ficheiro ainda não existir, devolvemos estrutura vazia
        return {"objects": [], "workspaces": [], "actions": []}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao ler cache: {str(e)}")

@app.post("/api/prompt")
def process_prompt(payload: PromptPayload):
    if not payload.prompt.strip():
        raise HTTPException(status_code=400, detail="O prompt não pode estar vazio.")
    
    try:
        result = run_pipeline(payload.prompt, payload.current_schema)
        return result
    except Exception as e:
        raise HTTPException(
            status_code=500, 
            detail=f"Erro interno no processamento: {str(e)}"
        )

@app.post("/api/save_cache")
def save_to_cache(payload: SaveCachePayload):
    try:
        with open("database/cache.json", "w", encoding="utf-8") as f:
            json.dump(payload.schema_data, f, ensure_ascii=False, indent=2)
            
        return {"success": True, "message": "Memória guardada com sucesso pelo utilizador!"}
    except Exception as e:
        raise HTTPException(
            status_code=500, 
            detail=f"Erro ao guardar na cache: {str(e)}"
        )
