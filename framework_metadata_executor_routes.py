# ===== framework_metadata_executor_routes.py =====
# Rotas FastAPI para executar metadata da framework.
#
# Segurança:
# - Dry-run por defeito.
# - Execução real exige:
#   AIBIZCORE_ENABLE_FRAMEWORK_EXECUTION=true
#   execute=true
#   dry_run=false
#   confirm_phrase="EXECUTE_FRAMEWORK_METADATA"

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from framework_metadata_executor import execute_framework_metadata


logger = logging.getLogger("framework_metadata_executor_routes")

router = APIRouter(prefix="/api", tags=["framework-metadata-executor"])


class FrameworkExecuteRequest(BaseModel):
    schema_data: Optional[Dict[str, Any]] = None
    schema: Optional[Dict[str, Any]] = None
    current_schema: Optional[Dict[str, Any]] = None
    data: Optional[Dict[str, Any]] = None
    dry_run: bool = True
    execute: bool = False
    confirm_phrase: Optional[str] = None


def _extract_schema(payload: FrameworkExecuteRequest) -> Dict[str, Any]:
    schema = (
        payload.schema_data
        or payload.schema
        or payload.current_schema
        or payload.data
    )

    if isinstance(schema, dict) and isinstance(schema.get("schema"), dict):
        schema = schema["schema"]

    if isinstance(schema, dict) and isinstance(schema.get("data"), dict):
        schema = schema["data"]

    if not isinstance(schema, dict):
        raise HTTPException(
            status_code=400,
            detail=(
                "Pedido inválido: envia o blueprint em 'schema_data', "
                "'schema', 'current_schema' ou 'data'."
            ),
        )

    if not isinstance(schema.get("objects", []), list):
        raise HTTPException(
            status_code=400,
            detail="Schema inválido: campo 'objects' deve ser uma lista.",
        )

    if not isinstance(schema.get("relations", []), list):
        raise HTTPException(
            status_code=400,
            detail="Schema inválido: campo 'relations' deve ser uma lista.",
        )

    return schema


@router.post("/framework-execute")
def framework_execute(payload: FrameworkExecuteRequest) -> Dict[str, Any]:
    """
    Dry-run ou execução real da metadata da framework.
    """
    schema = _extract_schema(payload)

    try:
        return execute_framework_metadata(
            schema,
            dry_run=payload.dry_run,
            execute=payload.execute,
            confirm_phrase=payload.confirm_phrase,
        )

    except Exception as exc:
        logger.exception("Erro na execução da metadata da framework")
        raise HTTPException(
            status_code=500,
            detail=f"Erro na execução da metadata da framework: {exc}",
        ) from exc


@router.post("/framework-metadata-execute")
def framework_metadata_execute_alias(payload: FrameworkExecuteRequest) -> Dict[str, Any]:
    """
    Alias de /api/framework-execute.
    """
    return framework_execute(payload)
