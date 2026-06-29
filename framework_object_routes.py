# ===== framework_object_routes.py =====

from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request

from framework_object_planner import plan_framework_metadata_from_blueprint


logger = logging.getLogger("framework_object_routes")

router = APIRouter(prefix="/api", tags=["framework-object-plan"])


def _extract_schema_from_raw_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Aceita estes formatos:

    1) Blueprint direto:
       {
         "objects": [...],
         "relations": [...]
       }

    2) Blueprint embrulhado:
       {
         "schema_data": {...}
       }

       ou:
       {
         "schema": {...}
       }

       ou:
       {
         "current_schema": {...}
       }

       ou:
       {
         "data": {...}
       }
    """
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=400,
            detail="Pedido inválido: o corpo deve ser um objeto JSON.",
        )

    if isinstance(payload.get("objects"), list):
        schema = payload
    else:
        schema = (
            payload.get("schema_data")
            or payload.get("schema")
            or payload.get("current_schema")
            or payload.get("data")
        )

    if isinstance(schema, dict) and isinstance(schema.get("schema"), dict):
        schema = schema["schema"]

    if isinstance(schema, dict) and isinstance(schema.get("data"), dict):
        schema = schema["data"]

    if not isinstance(schema, dict):
        raise HTTPException(
            status_code=400,
            detail=(
                "Pedido inválido: envia o blueprint diretamente ou dentro de "
                "'schema_data', 'schema', 'current_schema' ou 'data'."
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


@router.post("/framework-plan")
async def framework_plan(request: Request) -> Dict[str, Any]:
    """
    Gera um plano dry-run da metadata da framework.

    Não executa nada.
    Não escreve na base de dados.
    """
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail="Pedido inválido: corpo JSON em falta ou mal formado.",
        ) from exc

    schema = _extract_schema_from_raw_payload(payload)

    try:
        return plan_framework_metadata_from_blueprint(schema)

    except Exception as exc:
        logger.exception("Erro ao gerar plano de metadata da framework")
        raise HTTPException(
            status_code=500,
            detail=f"Erro ao gerar plano de metadata da framework: {exc}",
        ) from exc


@router.post("/framework-object-plan")
async def framework_object_plan_alias(request: Request) -> Dict[str, Any]:
    """
    Alias de /api/framework-plan.
    """
    return await framework_plan(request)
