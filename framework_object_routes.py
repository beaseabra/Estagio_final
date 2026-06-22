# ===== framework_object_routes.py =====
# Rotas FastAPI para gerar plano de metadata da framework.
# Modo seguro: dry-run, sem escrita na base de dados.

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from framework_object_planner import plan_framework_metadata_from_blueprint


logger = logging.getLogger("framework_object_routes")

router = APIRouter(prefix="/api", tags=["framework-object-plan"])


class FrameworkPlanRequest(BaseModel):
    """
    Aceita vários nomes para facilitar integração com o frontend.

    Preferido:
        schema_data

    Também aceite:
        schema
        current_schema
        data
    """

    schema_data: Optional[Dict[str, Any]] = Field(default=None)
    schema: Optional[Dict[str, Any]] = Field(default=None)
    current_schema: Optional[Dict[str, Any]] = Field(default=None)
    data: Optional[Dict[str, Any]] = Field(default=None)


def _extract_schema(payload: FrameworkPlanRequest) -> Dict[str, Any]:
    schema = (
        payload.schema_data
        or payload.schema
        or payload.current_schema
        or payload.data
    )

    if not isinstance(schema, dict):
        raise HTTPException(
            status_code=400,
            detail=(
                "Pedido inválido: envia o blueprint em 'schema_data', "
                "'schema', 'current_schema' ou 'data'."
            ),
        )

    if "schema" in schema and isinstance(schema.get("schema"), dict):
        schema = schema["schema"]

    elif "data" in schema and isinstance(schema.get("data"), dict):
        schema = schema["data"]

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
def framework_plan(payload: FrameworkPlanRequest) -> Dict[str, Any]:
    """
    Gera um plano dry-run da metadata da framework.

    Não executa nada.
    Não escreve na base de dados.
    """
    schema = _extract_schema(payload)

    try:
        return plan_framework_metadata_from_blueprint(schema)

    except Exception as exc:
        logger.exception("Erro ao gerar plano de metadata da framework")
        raise HTTPException(
            status_code=500,
            detail=f"Erro ao gerar plano de metadata da framework: {exc}",
        ) from exc


@router.post("/framework-object-plan")
def framework_object_plan_alias(payload: FrameworkPlanRequest) -> Dict[str, Any]:
    """
    Alias de /api/framework-plan.
    """
    return framework_plan(payload)
