# ===== api.py =====
# AiBizCore API v4.1
# Tratamento de erros estruturado em todas as rotas.
# Mantém o pipeline atual e acrescenta:
# - rota isolada de preview SQL Server
# - rotas isoladas de plano/dry-run/execução SQL Server

from __future__ import annotations

import json
import logging
import traceback
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from main import run_pipeline
from db_preview_routes import router as db_preview_router
from db_execution_routes import router as db_execution_router


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

# Rotas isoladas para preview da base de dados SQL Server.
# Não executam SQL e não interferem com o pipeline principal.
app.include_router(db_preview_router)

# Rotas isoladas para plano/dry-run/execução SQL Server.
# Por defeito funcionam em dry-run. Execução real exige confirmação explícita.
app.include_router(db_execution_router)


# ---------------------------------------------------------------------------
# Handler global de exceções — evita erros 500 silenciosos
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
    dialect: str = "sql_server"
    dry_run: bool = True
    connection_url: Optional[str] = None


# ---------------------------------------------------------------------------
# Rotas base
# ---------------------------------------------------------------------------

@app.get("/")
def health_check():
    return {
        "success": True,
        "status": "online",
        "version": "4.1",
        "message": "AiBizCore API a funcionar.",
        "routes": {
            "prompt": "/api/prompt",
            "save_cache": "/api/save_cache",
            "get_schema": "/api/get_schema",
            "db_preview": "/api/db-preview",
            "db_preview_alias": "/api/db_preview",
            "db_plan": "/api/db-plan",
            "db_execute": "/api/db-execute",
            "provision_db_legacy": "/api/provision_db",
            "regression_tests": "/api/regression_tests",
        },
    }


@app.get("/api/get_schema")
def get_schema():
    """
    Lê o schema guardado em database/cache.json.

    Devolve o blueprint completo usado pela aplicação:
    - objects
    - relations
    - actions
    - workspaces
    """
    try:
        with open("database/cache.json", "r", encoding="utf-8") as f:
            data = json.load(f)

        return {
            "success": True,
            "objects": data.get("objects", []),
            "relations": data.get("relations", []),
            "workspaces": data.get("workspaces", []),
            "actions": data.get("actions", []),
        }

    except FileNotFoundError:
        return {
            "success": True,
            "objects": [],
            "relations": [],
            "workspaces": [],
            "actions": [],
        }

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
    """
    Processa prompts do chat.

    Recebe:
    - prompt
    - current_schema opcional

    Encaminha para o pipeline principal em main.run_pipeline().
    """
    if not payload.prompt or not payload.prompt.strip():
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
                "trace": traceback.format_exc(),
            },
        )

    # Garantir resposta JSON estável quando o pipeline devolve texto puro.
    if isinstance(result, str):
        return {
            "success": True,
            "type": "CHAT",
            "message": result,
        }

    if isinstance(result, dict):
        return result

    return {
        "success": True,
        "type": "UNKNOWN",
        "data": result,
    }


@app.post("/api/save_cache")
def save_to_cache(payload: SaveCachePayload):
    """
    Guarda o schema atual em database/cache.json.
    """
    try:
        import os
        import shutil

        path = "database/cache.json"
        tmp = path + ".tmp"

        os.makedirs("database", exist_ok=True)

        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload.schema_data, f, ensure_ascii=False, indent=2)

        shutil.move(tmp, path)

        logger.info("Cache guardada com sucesso.")

        return {
            "success": True,
            "message": "Memória guardada com sucesso!",
        }

    except Exception as e:
        logger.exception("Erro ao guardar cache")
        raise HTTPException(status_code=500, detail=f"Erro ao guardar na cache: {e}")


@app.post("/api/provision_db")
def provision_db(payload: ProvisionPayload):
    """
    Rota legacy.

    Provisiona a base de dados a partir do schema recebido usando
    db_provisioning_agent.py.

    Nota:
    - Esta rota foi mantida para não partir compatibilidade.
    - O novo fluxo seguro usa:
        /api/db-plan
        /api/db-execute
    """
    try:
        from db_provisioning_agent import provision_database

        result = provision_database(
            schema=payload.schema_data,
            dialect=payload.dialect,
            connection_url=payload.connection_url,
            dry_run=payload.dry_run,
        )

        if not isinstance(result, dict):
            raise HTTPException(
                status_code=500,
                detail="provision_database devolveu um resultado inválido.",
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
    """
    Executa o Golden Dataset sem chamar o Ollama.
    Útil para validar o update_schema_handler.py.
    """
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
