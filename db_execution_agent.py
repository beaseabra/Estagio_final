# ===== db_execution_agent.py =====
# AiBizCore — Agente seguro para SQL Server
#
# Este ficheiro está pronto para receber dados de uma base SQL Server de teste.
#
# Segurança:
# - Por defeito NÃO executa SQL.
# - A execução real só acontece se:
#     dry_run=False
#     execute=True
#     confirm_phrase="EXECUTE_SQL_SERVER"
#     AIBIZCORE_ENABLE_DB_EXECUTION=true
#     SQLSERVER_CONNECTION_STRING configurada
# - A connection string fica no backend/.env, não no frontend.
# - Usa transação: se uma instrução falhar, faz rollback.

from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from sql_server_schema_adapter import convert_blueprint_to_sqlserver_schema


logger = logging.getLogger("db_execution_agent")
logging.basicConfig(level=logging.INFO, format="[%(name)s] %(levelname)s — %(message)s")


CONFIRM_PHRASE = "EXECUTE_SQL_SERVER"

ENABLE_EXECUTION_ENV = "AIBIZCORE_ENABLE_DB_EXECUTION"
CONNECTION_STRING_ENV = "SQLSERVER_CONNECTION_STRING"

SQLSERVER_DRIVER_ENV = "SQLSERVER_DRIVER"
SQLSERVER_SERVER_ENV = "SQLSERVER_SERVER"
SQLSERVER_PORT_ENV = "SQLSERVER_PORT"
SQLSERVER_DATABASE_ENV = "SQLSERVER_DATABASE"
SQLSERVER_USERNAME_ENV = "SQLSERVER_USERNAME"
SQLSERVER_PASSWORD_ENV = "SQLSERVER_PASSWORD"
SQLSERVER_TRUST_CERT_ENV = "SQLSERVER_TRUST_SERVER_CERTIFICATE"
SQLSERVER_ENCRYPT_ENV = "SQLSERVER_ENCRYPT"


def load_local_env(env_path: str = ".env") -> None:
    """
    Carrega variáveis de ambiente a partir de um ficheiro .env simples.
    Não precisa de python-dotenv.
    """
    path = Path(env_path)

    if not path.exists():
        return

    try:
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()

            if not line or line.startswith("#") or "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()

            if not key:
                continue

            if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
                value = value[1:-1]

            os.environ.setdefault(key, value)

    except Exception as exc:
        logger.warning("Não foi possível carregar .env: %s", exc)


load_local_env(".env")


def _env_enabled(value: Optional[str]) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _clean_env(name: str, default: str = "") -> str:
    return str(os.getenv(name, default) or "").strip()


def _mask_secret(value: Optional[str]) -> str:
    if not value:
        return ""

    text = str(value)

    if len(text) <= 4:
        return "****"

    return text[:2] + "***" + text[-2:]


def _build_connection_string_from_parts() -> Optional[str]:
    """
    Constrói connection string a partir de variáveis separadas.
    Só usa esta via se SQLSERVER_CONNECTION_STRING não estiver definida.
    """
    server = _clean_env(SQLSERVER_SERVER_ENV)
    database = _clean_env(SQLSERVER_DATABASE_ENV)
    username = _clean_env(SQLSERVER_USERNAME_ENV)
    password = _clean_env(SQLSERVER_PASSWORD_ENV)

    if not server or not database or not username or not password:
        return None

    driver = _clean_env(SQLSERVER_DRIVER_ENV, "ODBC Driver 18 for SQL Server")
    port = _clean_env(SQLSERVER_PORT_ENV)
    encrypt = _clean_env(SQLSERVER_ENCRYPT_ENV, "yes")
    trust_cert = _clean_env(SQLSERVER_TRUST_CERT_ENV, "yes")

    server_part = server

    # SQL Server usa vírgula para porta: 172.16.10.4,49702
    if port and "," not in server and ":" not in server:
        server_part = f"{server},{port}"

    return (
        f"DRIVER={{{driver}}};"
        f"SERVER={server_part};"
        f"DATABASE={database};"
        f"UID={username};"
        f"PWD={password};"
        f"Encrypt={encrypt};"
        f"TrustServerCertificate={trust_cert};"
    )


def get_connection_string(explicit_connection_string: Optional[str] = None) -> Optional[str]:
    """
    Prioridade:
    1. SQLSERVER_CONNECTION_STRING no ambiente/.env
    2. campos separados SQLSERVER_SERVER, SQLSERVER_DATABASE, etc.
    3. explicit_connection_string recebida no payload, apenas para testes manuais
    """
    env_value = _clean_env(CONNECTION_STRING_ENV)

    if env_value:
        return env_value

    built = _build_connection_string_from_parts()

    if built:
        return built

    if explicit_connection_string and explicit_connection_string.strip():
        return explicit_connection_string.strip()

    return None


def get_connection_config_status() -> Dict[str, Any]:
    """
    Devolve estado da configuração sem expor passwords.
    """
    connection_string = get_connection_string()

    return {
        "execution_enabled": _env_enabled(os.getenv(ENABLE_EXECUTION_ENV)),
        "has_connection_string": bool(connection_string),
        "connection_string_source": (
            CONNECTION_STRING_ENV
            if _clean_env(CONNECTION_STRING_ENV)
            else "parts"
            if _build_connection_string_from_parts()
            else None
        ),
        "required_enable": f"{ENABLE_EXECUTION_ENV}=true",
        "server": _clean_env(SQLSERVER_SERVER_ENV),
        "port": _clean_env(SQLSERVER_PORT_ENV),
        "database": _clean_env(SQLSERVER_DATABASE_ENV),
        "username": _clean_env(SQLSERVER_USERNAME_ENV),
        "password_configured": bool(_clean_env(SQLSERVER_PASSWORD_ENV)),
        "driver": _clean_env(SQLSERVER_DRIVER_ENV, "ODBC Driver 18 for SQL Server"),
        "encrypt": _clean_env(SQLSERVER_ENCRYPT_ENV, "yes"),
        "trust_server_certificate": _clean_env(SQLSERVER_TRUST_CERT_ENV, "yes"),
    }


def _extract_schema(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extrai o blueprint real a partir de vários formatos possíveis.
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
    Gera plano SQL Server sem executar nada.
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

    return {
        "success": True,
        "mode": "dry_run",
        "database_target": "sql_server",
        "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "will_execute": False,
        "requires_confirmation": True,
        "confirmation_phrase": CONFIRM_PHRASE,
        "execution_safety": {
            **get_connection_config_status(),
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


def test_sql_server_connection(
    *,
    connection_string: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Testa ligação ao SQL Server sem criar tabelas.
    Executa apenas:
        SELECT 1
        SELECT DB_NAME()
    """
    resolved_connection_string = get_connection_string(connection_string)
    config_status = get_connection_config_status()

    if not resolved_connection_string:
        return {
            "success": False,
            "mode": "connection_test",
            "connected": False,
            "error": (
                "Connection string não configurada. Preenche SQLSERVER_CONNECTION_STRING "
                "ou os campos SQLSERVER_SERVER, SQLSERVER_DATABASE, SQLSERVER_USERNAME e SQLSERVER_PASSWORD."
            ),
            "config": config_status,
        }

    try:
        import pyodbc
    except ImportError:
        return {
            "success": False,
            "mode": "connection_test",
            "connected": False,
            "error": (
                "pyodbc não está instalado neste ambiente Python. "
                "Dentro do venv tenta: python -m pip install pyodbc"
            ),
            "config": config_status,
        }

    try:
        conn = pyodbc.connect(resolved_connection_string, timeout=10, autocommit=True)
        cursor = conn.cursor()

        cursor.execute("SELECT 1 AS ok")
        ok_row = cursor.fetchone()

        cursor.execute("SELECT DB_NAME() AS current_database")
        db_row = cursor.fetchone()

        cursor.close()
        conn.close()

        return {
            "success": True,
            "mode": "connection_test",
            "connected": True,
            "message": "Ligação ao SQL Server validada com sucesso.",
            "select_1": ok_row[0] if ok_row else None,
            "current_database": db_row[0] if db_row else None,
            "config": {
                **config_status,
                "password": _mask_secret(_clean_env(SQLSERVER_PASSWORD_ENV)),
            },
        }

    except Exception as exc:
        return {
            "success": False,
            "mode": "connection_test",
            "connected": False,
            "error": str(exc),
            "config": config_status,
        }


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

    resolved_connection_string = get_connection_string(connection_string)

    if not resolved_connection_string:
        return {
            "success": False,
            "mode": "blocked",
            "will_execute": False,
            "executed": False,
            "error": (
                "Connection string não configurada. Preenche SQLSERVER_CONNECTION_STRING "
                "ou as variáveis separadas SQLSERVER_SERVER, SQLSERVER_DATABASE, SQLSERVER_USERNAME e SQLSERVER_PASSWORD."
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
                "pyodbc não está instalado neste ambiente Python. "
                "Dentro do venv tenta: python -m pip install pyodbc"
            ),
            "plan": plan,
        }

    executable_statements = plan.get("executable_statements", [])
    execution_log: List[Dict[str, Any]] = []

    logger.info("A iniciar execução SQL Server: %d statements", len(executable_statements))

    conn = None

    try:
        conn = pyodbc.connect(resolved_connection_string, timeout=15, autocommit=False)
        cursor = conn.cursor()

        for idx, statement in enumerate(executable_statements, start=1):
            try:
                cursor.execute(statement)
                execution_log.append({
                    "index": idx,
                    "success": True,
                    "statement_preview": statement[:260],
                })
            except Exception as statement_error:
                execution_log.append({
                    "index": idx,
                    "success": False,
                    "statement_preview": statement[:260],
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
