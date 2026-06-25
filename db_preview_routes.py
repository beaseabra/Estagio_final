# ===== db_preview_routes.py =====
# Rotas isoladas para gerar preview de base de dados SQL Server.
# Não executa SQL. Não altera o schema atual. Não mexe no pipeline do chat.
#
# Requer:
#   - sql_server_schema_adapter.py na raiz do projeto
#   - em api.py: from db_preview_routes import router as db_preview_router
#   - em api.py: app.include_router(db_preview_router)

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from sql_server_schema_adapter import convert_blueprint_to_sqlserver_schema


logger = logging.getLogger("db_preview_routes")

router = APIRouter(prefix="/api", tags=["database-preview"])


class DBPreviewRequest(BaseModel):
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


def _extract_schema(payload: DBPreviewRequest) -> Dict[str, Any]:
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

    # Se vier embrulhado no formato de resposta do AiBizCore.
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


@router.post("/db-preview")
def preview_database_schema(payload: DBPreviewRequest) -> Dict[str, Any]:
    """
    Gera preview do formato SQL Server a partir do blueprint atual.

    Usa apenas:
        - objects
        - relations

    Ignora:
        - actions
        - workspaces

    Não executa nada na base de dados.
    """

    schema = _extract_schema(payload)

    try:
        result = convert_blueprint_to_sqlserver_schema(schema)
        return result

    except Exception as exc:
        logger.exception("Erro ao gerar preview SQL Server")
        raise HTTPException(
            status_code=500,
            detail=f"Erro ao gerar preview SQL Server: {exc}",
        ) from exc


@router.post("/db_preview")
def preview_database_schema_alias(payload: DBPreviewRequest) -> Dict[str, Any]:
    """
    Alias da rota /api/db-preview.
    Útil se preferires underscore no frontend.
    """
    return preview_database_schema(payload)
