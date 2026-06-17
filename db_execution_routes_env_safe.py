# ===== db_execution_routes.py =====
# Rotas isoladas para dry-run e execução real SQL Server.
# Não substitui /api/db-preview.
# Não mexe no pipeline do chat.

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from db_execution_agent import execute_sql_server_plan


logger = logging.getLogger("db_execution_routes")

router = APIRouter(prefix="/api", tags=["database-execution"])


class DBExecutionRequest(BaseModel):
    """
    Payload flexível para aceitar vários formatos vindos do frontend.

    Para dry-run:
        {
          "schema_data": {...},
          "dry_run": true
        }

    Para execução real:
        {
          "schema_data": {...},
          "dry_run": false,
          "execute": true,
          "confirm_phrase": "EXECUTE_SQL_SERVER"
        }

    Nota:
    - A connection string deve estar no backend como variável de ambiente:
      SQLSERVER_CONNECTION_STRING
    - Evita-se enviar passwords pelo frontend.
    """

    schema_data: Optional[Dict[str, Any]] = Field(default=None)
    current_schema: Optional[Dict[str, Any]] = Field(default=None)
    blueprint: Optional[Dict[str, Any]] = Field(default=None)
    data: Optional[Dict[str, Any]] = Field(default=None)

    dry_run: bool = True
    execute: bool = False
    confirm_phrase: Optional[str] = None

    # Mantido opcional para testes locais, mas o fluxo recomendado é ENV no backend.
    connection_string: Optional[str] = None


def _payload_to_dict(payload: DBExecutionRequest) -> Dict[str, Any]:
    schema_payload = (
        payload.schema_data
        or payload.current_schema
        or payload.blueprint
        or payload.data
    )

    if not isinstance(schema_payload, dict):
        raise HTTPException(
            status_code=400,
            detail="Pedido inválido: envia o blueprint em 'schema_data', 'current_schema', 'blueprint' ou 'data'.",
        )

    return schema_payload


@router.post("/db-plan")
def db_plan(payload: DBExecutionRequest) -> Dict[str, Any]:
    """
    Gera plano de execução SQL Server em dry-run.
    Nunca executa SQL.
    """
    try:
        schema_payload = _payload_to_dict(payload)

        return execute_sql_server_plan(
            schema_payload,
            dry_run=True,
            execute=False,
            connection_string=None,
            confirm_phrase=None,
        )

    except HTTPException:
        raise

    except Exception as exc:
        logger.exception("Erro ao gerar plano SQL Server")
        raise HTTPException(
            status_code=500,
            detail=f"Erro ao gerar plano SQL Server: {exc}",
        ) from exc


@router.post("/db-execute")
def db_execute(payload: DBExecutionRequest) -> Dict[str, Any]:
    """
    Executa ou simula execução SQL Server.

    Por defeito é dry-run.
    Só executa realmente se:
    - dry_run=False
    - execute=True
    - confirm_phrase="EXECUTE_SQL_SERVER"
    - AIBIZCORE_ENABLE_DB_EXECUTION=true no backend
    - SQLSERVER_CONNECTION_STRING definido no backend
    """
    try:
        schema_payload = _payload_to_dict(payload)

        return execute_sql_server_plan(
            schema_payload,
            dry_run=payload.dry_run,
            execute=payload.execute,
            confirm_phrase=payload.confirm_phrase,
            connection_string=payload.connection_string,
        )

    except HTTPException:
        raise

    except Exception as exc:
        logger.exception("Erro na rota db-execute")
        raise HTTPException(
            status_code=500,
            detail=f"Erro na execução SQL Server: {exc}",
        ) from exc
