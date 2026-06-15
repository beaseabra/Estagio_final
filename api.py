# ===== api.py =====
# FIX: tratamento de erros em TODAS as rotas — zero erros 500 silenciosos.
# Todos os exceptions são capturados, logged e devolvidos como JSON estruturado.

import json
import logging
import traceback

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import List, Optional, Dict, Any

from main import run_pipeline

logger = logging.getLogger("aibizcore.api")
logging.basicConfig(
    level=logging.INFO,
    format="[%(name)s] %(levelname)s — %(message)s",
)

app = FastAPI(
    title="AiBizCore API",
    description="API do Protótipo AiBizCore v4.1 — Production-Grade",
    version="4.1",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Handler global de exceções — elimina erros 500 silenciosos
# ---------------------------------------------------------------------------

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    tb = traceback.format_exc()
    logger.error("Exceção não tratada em %s:\n%s", request.url.path, tb)
    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "error": str(exc),
            "type": type(exc).__name__,
            "path": str(request.url.path),
        },
    )


# ---------------------------------------------------------------------------
# Modelos de Request
# ---------------------------------------------------------------------------

class MessageObject(BaseModel):
    role: str
    content: str


class PromptPayload(BaseModel):
    prompt: str
    history: Optional[List[MessageObject]] = []
    current_schema: Optional[Dict[str, Any]] = None


class SaveCachePayload(BaseModel):
    schema_data: Dict[str, Any]


class ProvisionPayload(BaseModel):
    schema_data: Dict[str, Any]
    dialect: str = "postgresql"
    dry_run: bool = True
    connection_url: Optional[str] = None


# ---------------------------------------------------------------------------
# Rotas
# ---------------------------------------------------------------------------

@app.get("/")
def health_check():
    return {
        "status": "online",
        "version": "4.1",
        "message": "AiBizCore API a funcionar.",
    }


@app.get("/api/get_schema")
def get_schema():
    try:
        with open("database/cache.json", "r", encoding="utf-8") as f:
            data = json.load(f)
        return {
            "objects":    data.get("objects", []),
            "workspaces": data.get("workspaces", []),
            "actions":    data.get("actions", []),
        }
    except FileNotFoundError:
        return {"objects": [], "workspaces": [], "actions": []}
    except json.JSONDecodeError as e:
        logger.error("cache.json corrompido: %s", e)
        raise HTTPException(
            status_code=500,
            detail=f"cache.json corrompido: {e}. Apaga o ficheiro e reinicia.",
        )
    except Exception as e:
        logger.exception("Erro ao ler cache")
        raise HTTPException(status_code=500, detail=f"Erro ao ler cache: {e}")


@app.post("/api/prompt")
def process_prompt(payload: PromptPayload):
    if not payload.prompt.strip():
        raise HTTPException(status_code=400, detail="O prompt não pode estar vazio.")

    logger.info("Prompt recebido: '%s'", payload.prompt[:80])

    try:
        result = run_pipeline(payload.prompt, payload.current_schema)
    except Exception as e:
        logger.exception("Erro no pipeline para prompt: '%s'", payload.prompt[:80])
        raise HTTPException(
            status_code=500,
            detail={
                "message": f"Erro interno no pipeline: {e}",
                "type": type(e).__name__,
                # Devolve o traceback completo em modo de desenvolvimento
                "trace": traceback.format_exc(),
            },
        )

    # Garantir que o resultado é sempre serializável
    if isinstance(result, str):
        return {"success": True, "type": "CHAT", "message": result}

    return result


@app.post("/api/save_cache")
def save_to_cache(payload: SaveCachePayload):
    try:
        import os, shutil
        path = "database/cache.json"
        tmp  = path + ".tmp"
        os.makedirs("database", exist_ok=True)

        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload.schema_data, f, ensure_ascii=False, indent=2)

        shutil.move(tmp, path)
        logger.info("Cache guardada com sucesso.")
        return {"success": True, "message": "Memória guardada com sucesso!"}

    except Exception as e:
        logger.exception("Erro ao guardar cache")
        raise HTTPException(status_code=500, detail=f"Erro ao guardar na cache: {e}")


@app.post("/api/provision_db")
def provision_db(payload: ProvisionPayload):
    """Provisiona a base de dados a partir do schema guardado."""
    try:
        from db_provisioning_agent import provision_database
        result = provision_database(
            schema=payload.schema_data,
            dialect=payload.dialect,
            connection_url=payload.connection_url,
            dry_run=payload.dry_run,
        )
        if not result.get("success"):
            raise HTTPException(status_code=400, detail=result)
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Erro no provision_db")
        raise HTTPException(status_code=500, detail=f"Erro no provisionamento: {e}")


@app.get("/api/regression_tests")
def run_regression_tests():
    """Executa o Golden Dataset sem chamar o Ollama. Útil para CI/CD."""
    try:
        from handlers.update_schema_handler import run_regression_tests as _run
        results = _run()
        sumario = results.get("_sumario", {})
        all_passed = sumario.get("passou") == sumario.get("total")
        return {
            "success": all_passed,
            "results": results,
        }
    except Exception as e:
        logger.exception("Erro nos testes de regressão")
        raise HTTPException(status_code=500, detail=f"Erro nos testes: {e}")
