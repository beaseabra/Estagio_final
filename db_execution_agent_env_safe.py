# ===== db_execution_agent.py =====
# AiBizCore — Agente seguro de execução SQL Server
#
# Fluxo:
# 1. build_sql_server_execution_plan(...)  -> dry-run / plano
# 2. execute_sql_server_plan(...)          -> dry-run por defeito
# 3. execução real só acontece se:
#      - dry_run=False
#      - execute=True
#      - confirm_phrase="EXECUTE_SQL_SERVER"
#      - variável de ambiente AIBIZCORE_ENABLE_DB_EXECUTION=true
#      - variável de ambiente SQLSERVER_CONNECTION_STRING definida
#
# Segurança:
# - A connection string fica no backend / ambiente, não no frontend.
# - O frontend nunca precisa de saber passwords.
# - Usa transação: se uma instrução falhar, faz rollback.

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from sql_server_schema_adapter import convert_blueprint_to_sqlserver_schema


logger = logging.getLogger("db_execution_agent")
logging.basicConfig(level=logging.INFO, format="[%(name)s] %(levelname)s — %(message)s")


CONFIRM_PHRASE = "EXECUTE_SQL_SERVER"
ENABLE_EXECUTION_ENV = "AIBIZCORE_ENABLE_DB_EXECUTION"
CONNECTION_STRING_ENV = "SQLSERVER_CONNECTION_STRING"


def _env_enabled(value: Optional[str]) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _get_connection_string(explicit_connection_string: Optional[str] = None) -> Optional[str]:
    """
    Regra de segurança:
    - preferimos sempre a variável de ambiente.
    - só usamos explicit_connection_string se vier explicitamente no payload,
      mas o fluxo recomendado é NÃO enviar passwords pelo frontend.
    """
    env_value = os.getenv(CONNECTION_STRING_ENV)

    if env_value and env_value.strip():
        return env_value.strip()

    if explicit_connection_string and explicit_connection_string.strip():
        return explicit_connection_string.strip()

    return None


def _extract_schema(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extrai o blueprint real a partir de vários formatos possíveis.

    Aceita:
    - blueprint direto: {"objects": [...], "relations": [...]}
    - resposta do backend: {"schema": {...}}
    - resposta do backend: {"data": {...}}
    - wrapper: {"schema_data": {...}}
    """
    if not isinstance(payload, dict):
        raise ValueError("Payload inválido: esperava um dict.")

    schema = (
        payload.get("schema_data")
        or payload.get("schema")
        or payload.get("data")
        or payload.get("blueprint")
        or payload
    )

    if isinstance(schema, dict) and isinstance(schema.get("schema"), dict):
        schema = schema["schema"]

    if isinstance(schema, dict) and isinstance(schema.get("data"), dict):
        schema = schema["data"]

    if not isinstance(schema, dict):
        raise ValueError("Schema inválido: não foi possível extrair um blueprint.")

    if not isinstance(schema.get("objects", []), list):
        raise ValueError("Schema inválido: 'objects' deve ser uma lista.")

    if not isinstance(schema.get("relations", []), list):
        raise ValueError("Schema inválido: 'relations' deve ser uma lista.")

    return schema


def _build_statements(sql_preview: List[str]) -> List[str]:
    statements: List[str] = []

    for statement in sql_preview or []:
        statement = str(statement or "").strip()

        if not statement:
            continue

        statements.append(statement)

    return statements


def _is_executable_statement(statement: str) -> bool:
    clean = str(statement or "").strip()

    if not clean:
        return False

    if clean.startswith("--"):
        return False

    return True


def build_sql_server_execution_plan(schema_payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Gera plano de execução SQL Server sem executar nada.
    """
    schema = _extract_schema(schema_payload)
    preview = convert_blueprint_to_sqlserver_schema(schema)

    if not preview.get("success"):
        return {
            "success": False,
            "mode": "dry_run",
            "error": "Falha ao converter blueprint para SQL Server.",
            "preview": preview,
        }

    statements = _build_statements(preview.get("sql_preview", []))
    executable_statements = [s for s in statements if _is_executable_statement(s)]

    tables = preview.get("tables", [])
    relations = preview.get("relations", [])

    execution_enabled = _env_enabled(os.getenv(ENABLE_EXECUTION_ENV))
    has_connection_string = bool(_get_connection_string())

    plan = {
        "success": True,
        "mode": "dry_run",
        "database_target": "sql_server",
        "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "will_execute": False,
        "requires_confirmation": True,
        "confirmation_phrase": CONFIRM_PHRASE,
        "execution_safety": {
            "real_execution_enabled": execution_enabled,
            "connection_string_configured": has_connection_string,
            "connection_string_source": "env" if os.getenv(CONNECTION_STRING_ENV) else None,
            "required_env_enable": f"{ENABLE_EXECUTION_ENV}=true",
            "required_env_connection": CONNECTION_STRING_ENV,
        },
        "summary": {
            "tables_count": len(tables),
            "relations_count": len(relations),
            "statements_count": len(executable_statements),
            "warnings_count": len(preview.get("warnings", [])),
        },
        "tables": [
            {
                "source_object": table.get("source_object"),
                "table_name": table.get("table_name"),
                "primary_key": table.get("primary_key"),
                "fields_count": len(table.get("fields", [])),
                "fields": table.get("fields", []),
            }
            for table in tables
        ],
        "relations": relations,
        "statements": statements,
        "executable_statements": executable_statements,
        "warnings": preview.get("warnings", []),
        "ignored_blueprint_sections": preview.get("ignored_blueprint_sections", []),
        "mapping": preview.get("mapping", {}),
        "message": "Dry-run concluído. Nenhuma alteração foi feita na base de dados.",
    }

    return plan


def execute_sql_server_plan(
    schema_payload: Dict[str, Any],
    *,
    connection_string: Optional[str] = None,
    dry_run: bool = True,
    execute: bool = False,
    confirm_phrase: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Executa ou simula a execução do plano SQL Server.

    Modo normal:
        dry_run=True
        execute=False

    Modo real:
        dry_run=False
        execute=True
        confirm_phrase="EXECUTE_SQL_SERVER"
        AIBIZCORE_ENABLE_DB_EXECUTION=true
        SQLSERVER_CONNECTION_STRING="DRIVER={...};SERVER=...;DATABASE=...;UID=...;PWD=...;"
    """
    plan = build_sql_server_execution_plan(schema_payload)

    if not plan.get("success"):
        return plan

    if dry_run or not execute:
        plan["mode"] = "dry_run"
        plan["will_execute"] = False
        plan["message"] = "Dry-run concluído. Nenhuma alteração foi feita na base de dados."
        return plan

    if confirm_phrase != CONFIRM_PHRASE:
        return {
            "success": False,
            "mode": "blocked",
            "will_execute": False,
            "executed": False,
            "error": (
                "Execução bloqueada. A confirmação explícita não corresponde. "
                f'Escreve exatamente "{CONFIRM_PHRASE}".'
            ),
            "plan": plan,
        }

    execution_enabled = _env_enabled(os.getenv(ENABLE_EXECUTION_ENV))

    if not execution_enabled:
        return {
            "success": False,
            "mode": "blocked",
            "will_execute": False,
            "executed": False,
            "error": (
                "Execução real bloqueada no backend. "
                f"Define {ENABLE_EXECUTION_ENV}=true para permitir execução real."
            ),
            "plan": plan,
        }

    resolved_connection_string = _get_connection_string(connection_string)

    if not resolved_connection_string:
        return {
            "success": False,
            "mode": "blocked",
            "will_execute": False,
            "executed": False,
            "error": (
                "Connection string não configurada. "
                f"Define a variável de ambiente {CONNECTION_STRING_ENV} no backend."
            ),
            "plan": plan,
        }

    try:
        import pyodbc
    except ImportError:
        return {
            "success": False,
            "mode": "blocked",
            "will_execute": False,
            "executed": False,
            "error": (
                "pyodbc não está instalado neste ambiente. "
                "Instala com: pip install pyodbc"
            ),
            "plan": plan,
        }

    executable_statements = plan.get("executable_statements", [])
    execution_log: List[Dict[str, Any]] = []

    logger.info(
        "A iniciar execução SQL Server: %d statements",
        len(executable_statements),
    )

    conn = None

    try:
        conn = pyodbc.connect(resolved_connection_string, autocommit=False)
        cursor = conn.cursor()

        for idx, statement in enumerate(executable_statements, start=1):
            try:
                cursor.execute(statement)
                execution_log.append({
                    "index": idx,
                    "success": True,
                    "statement_preview": statement[:220],
                })
            except Exception as statement_error:
                execution_log.append({
                    "index": idx,
                    "success": False,
                    "statement_preview": statement[:220],
                    "error": str(statement_error),
                })
                raise

        conn.commit()

        return {
            "success": True,
            "mode": "execute",
            "will_execute": True,
            "executed": True,
            "message": "Execução concluída com sucesso no SQL Server.",
            "summary": plan.get("summary", {}),
            "execution_log": execution_log,
            "warnings": plan.get("warnings", []),
            "tables": plan.get("tables", []),
            "relations": plan.get("relations", []),
        }

    except Exception as exc:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass

        logger.exception("Erro durante execução SQL Server")

        return {
            "success": False,
            "mode": "execute",
            "will_execute": True,
            "executed": False,
            "message": "Execução falhou. Foi feito rollback da transação.",
            "error": str(exc),
            "execution_log": execution_log,
            "summary": plan.get("summary", {}),
            "warnings": plan.get("warnings", []),
        }

    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
