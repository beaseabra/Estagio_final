# ===== db_execution_agent.py =====
# AiBizCore — Agente seguro de execução SQL Server
#
# Objetivo:
# - Receber o blueprint atual.
# - Converter para o formato SQL Server do orientador usando sql_server_schema_adapter.py.
# - Produzir um plano de execução em dry_run.
# - Só executar SQL real se houver confirmação explícita.
#
# Segurança:
# - Por defeito NÃO executa SQL.
# - Para executar é obrigatório:
#     dry_run=False
#     execute=True
#     confirm_phrase="EXECUTE_SQL_SERVER"
#     connection_string preenchida
# - Usa transação. Se uma query falhar, faz rollback.

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from sql_server_schema_adapter import convert_blueprint_to_sqlserver_schema


logger = logging.getLogger("db_execution_agent")
logging.basicConfig(level=logging.INFO, format="[%(name)s] %(levelname)s — %(message)s")


CONFIRM_PHRASE = "EXECUTE_SQL_SERVER"


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
    """
    O adapter devolve uma lista em que cada elemento já é uma instrução ou bloco SQL.
    Aqui limpamos comentários vazios e espaços.
    """
    statements: List[str] = []

    for statement in sql_preview or []:
        statement = str(statement or "").strip()

        if not statement:
            continue

        # Mantemos comentários no dry_run, mas não os executamos no modo real.
        statements.append(statement)

    return statements


def _is_executable_statement(statement: str) -> bool:
    """
    Evita executar linhas meramente informativas.
    """
    clean = statement.strip()

    if not clean:
        return False

    if clean.startswith("--"):
        return False

    return True


def build_sql_server_execution_plan(schema_payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Gera plano de execução SQL Server sem executar nada.

    Usa apenas:
    - objects
    - relations

    Ignora:
    - actions
    - workspaces
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

    plan = {
        "success": True,
        "mode": "dry_run",
        "database_target": "sql_server",
        "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "will_execute": False,
        "requires_confirmation": True,
        "confirmation_phrase": CONFIRM_PHRASE,
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
        connection_string="DRIVER={...};SERVER=...;DATABASE=...;UID=...;PWD=...;"
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
            "error": (
                "Execução bloqueada. Para executar SQL real, envia "
                f'confirm_phrase="{CONFIRM_PHRASE}".'
            ),
            "plan": plan,
        }

    if not connection_string or not str(connection_string).strip():
        return {
            "success": False,
            "mode": "blocked",
            "will_execute": False,
            "error": "connection_string é obrigatória para executar no SQL Server.",
            "plan": plan,
        }

    try:
        import pyodbc
    except ImportError:
        return {
            "success": False,
            "mode": "blocked",
            "will_execute": False,
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
        conn = pyodbc.connect(connection_string, autocommit=False)
        cursor = conn.cursor()

        for idx, statement in enumerate(executable_statements, start=1):
            try:
                cursor.execute(statement)
                execution_log.append({
                    "index": idx,
                    "success": True,
                    "statement_preview": statement[:180],
                })
            except Exception as statement_error:
                execution_log.append({
                    "index": idx,
                    "success": False,
                    "statement_preview": statement[:180],
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
