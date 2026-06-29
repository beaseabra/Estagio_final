# ===== db_execution_agent.py =====

from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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


# ---------------------------------------------------------------------
# .env loader simples, sem dependências externas
# ---------------------------------------------------------------------

def load_local_env(env_path: str = ".env") -> None:
    """
    Carrega variáveis de ambiente a partir de um ficheiro .env simples.
    Não precisa de python-dotenv.

    Variáveis já existentes no ambiente não são sobrescritas.
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


# ---------------------------------------------------------------------
# Helpers de configuração
# ---------------------------------------------------------------------

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
    server = _clean_env(SQLSERVER_SERVER_ENV)
    database = _clean_env(SQLSERVER_DATABASE_ENV)
    username = _clean_env(SQLSERVER_USERNAME_ENV)
    password = _clean_env(SQLSERVER_PASSWORD_ENV)

    if not server or not database or not username or not password:
        return None

    driver = _clean_env(SQLSERVER_DRIVER_ENV, "pymssql")
    port = _clean_env(SQLSERVER_PORT_ENV)
    encrypt = _clean_env(SQLSERVER_ENCRYPT_ENV, "yes")
    trust_cert = _clean_env(SQLSERVER_TRUST_CERT_ENV, "yes")

    server_part = server

    if port and "," not in server and ":" not in server:
        server_part = f"{server},{port}"

    return (
        f"DRIVER={{{driver}}};"
        f"SERVER={server_part};"
        f"DATABASE={database};"
        f"UID={username};"
        f"PWD=***hidden***;"
        f"Encrypt={encrypt};"
        f"TrustServerCertificate={trust_cert};"
    )


def get_connection_string(explicit_connection_string: Optional[str] = None) -> Optional[str]:
    env_value = _clean_env(CONNECTION_STRING_ENV)

    if env_value:
        return env_value

    built = _build_connection_string_from_parts()

    if built:
        return built

    if explicit_connection_string and explicit_connection_string.strip():
        return explicit_connection_string.strip()

    return None


def get_connection_parts() -> Tuple[str, int, str, str, str]:
    """
    Devolve os campos necessários para pymssql.connect().
    """
    server = _clean_env(SQLSERVER_SERVER_ENV)
    port_raw = _clean_env(SQLSERVER_PORT_ENV, "1433")
    database = _clean_env(SQLSERVER_DATABASE_ENV)
    username = _clean_env(SQLSERVER_USERNAME_ENV)
    password = _clean_env(SQLSERVER_PASSWORD_ENV)

    missing = []

    if not server:
        missing.append(SQLSERVER_SERVER_ENV)

    if not database:
        missing.append(SQLSERVER_DATABASE_ENV)

    if not username:
        missing.append(SQLSERVER_USERNAME_ENV)

    if not password:
        missing.append(SQLSERVER_PASSWORD_ENV)

    if missing:
        raise ValueError("Configuração SQL Server incompleta. Faltam: " + ", ".join(missing))

    try:
        port = int(port_raw)
    except ValueError as exc:
        raise ValueError(f"{SQLSERVER_PORT_ENV} inválido: {port_raw!r}") from exc

    return server, port, database, username, password


def get_connection_config_status() -> Dict[str, Any]:
    """
    Devolve estado da configuração sem expor passwords.
    """
    try:
        get_connection_parts()
        has_parts = True
    except Exception:
        has_parts = False

    return {
        "execution_enabled": _env_enabled(os.getenv(ENABLE_EXECUTION_ENV)),
        "has_connection_string": has_parts or bool(_clean_env(CONNECTION_STRING_ENV)),
        "connection_string_source": "parts" if has_parts else CONNECTION_STRING_ENV if _clean_env(CONNECTION_STRING_ENV) else None,
        "required_enable": f"{ENABLE_EXECUTION_ENV}=true",
        "server": _clean_env(SQLSERVER_SERVER_ENV),
        "port": _clean_env(SQLSERVER_PORT_ENV),
        "database": _clean_env(SQLSERVER_DATABASE_ENV),
        "username": _clean_env(SQLSERVER_USERNAME_ENV),
        "password_configured": bool(_clean_env(SQLSERVER_PASSWORD_ENV)),
        "driver": "pymssql",
        "encrypt": _clean_env(SQLSERVER_ENCRYPT_ENV, "yes"),
        "trust_server_certificate": _clean_env(SQLSERVER_TRUST_CERT_ENV, "yes"),
    }


# ---------------------------------------------------------------------
# Blueprint/schema helpers
# ---------------------------------------------------------------------

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


# ---------------------------------------------------------------------
# Plano SQL Server
# ---------------------------------------------------------------------

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
            "required_env_connection": (
                "SQLSERVER_SERVER, SQLSERVER_PORT, SQLSERVER_DATABASE, "
                "SQLSERVER_USERNAME, SQLSERVER_PASSWORD"
            ),
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


# ---------------------------------------------------------------------
# Teste de ligação
# ---------------------------------------------------------------------

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
    config_status = get_connection_config_status()

    try:
        server, port, database, username, password = get_connection_parts()
    except Exception as exc:
        return {
            "success": False,
            "mode": "connection_test",
            "connected": False,
            "error": str(exc),
            "config": config_status,
        }

    try:
        import pymssql
    except ImportError:
        return {
            "success": False,
            "mode": "connection_test",
            "connected": False,
            "error": (
                "pymssql não está instalado neste ambiente Python. "
                "Instala com: python3 -m pip install --user --break-system-packages pymssql"
            ),
            "config": config_status,
        }

    try:
        conn = pymssql.connect(
            server=server,
            port=port,
            user=username,
            password=password,
            database=database,
            login_timeout=10,
            timeout=10,
        )

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
            "config": config_status,
        }

    except Exception as exc:
        return {
            "success": False,
            "mode": "connection_test",
            "connected": False,
            "error": str(exc),
            "config": config_status,
        }




# ---------------------------------------------------------------------
# Introspection / preflight antes de executar
# ---------------------------------------------------------------------

def _connect_pymssql():
    """
    Abre ligação pymssql usando os campos do .env.
    """
    import pymssql

    server, port, database, username, password = get_connection_parts()

    return pymssql.connect(
        server=server,
        port=port,
        user=username,
        password=password,
        database=database,
        login_timeout=15,
        timeout=30,
    )


def _fetch_existing_tables(cursor, table_names: List[str]) -> List[str]:
    """
    Devolve a lista de tabelas do plano que já existem na base.
    """
    clean_names = sorted({str(name).strip() for name in table_names if str(name).strip()})

    if not clean_names:
        return []

    placeholders = ",".join(["%s"] * len(clean_names))

    cursor.execute(
        f"""
        SELECT TABLE_NAME
        FROM INFORMATION_SCHEMA.TABLES
        WHERE TABLE_TYPE = 'BASE TABLE'
          AND TABLE_NAME IN ({placeholders})
        """,
        tuple(clean_names),
    )

    return sorted(row[0] for row in cursor.fetchall())


def _check_procedure_exists(cursor, procedure_name: str) -> bool:
    """
    Confirma se uma stored procedure existe na base atual.
    """
    cursor.execute(
        """
        SELECT COUNT(*)
        FROM sys.procedures
        WHERE name = %s
        """,
        (procedure_name,),
    )

    row = cursor.fetchone()
    return bool(row and row[0] > 0)


def _check_table_exists(cursor, table_name: str) -> bool:
    """
    Confirma se uma tabela existe na base atual.
    """
    cursor.execute(
        """
        SELECT COUNT(*)
        FROM INFORMATION_SCHEMA.TABLES
        WHERE TABLE_TYPE = 'BASE TABLE'
          AND TABLE_NAME = %s
        """,
        (table_name,),
    )

    row = cursor.fetchone()
    return bool(row and row[0] > 0)


def run_database_preflight(cursor, plan: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validação antes da execução real.

    Objetivo:
    - impedir que o agente tente criar tabelas que já existem;
    - confirmar dependências mínimas da framework;
    - evitar execuções parciais em modo autocommit.
    """
    table_names = [table.get("table_name") for table in plan.get("tables", [])]
    existing_tables = _fetch_existing_tables(cursor, table_names)

    procedure_exists = _check_procedure_exists(cursor, "sp_sys_create_ID_Relation")
    csysuser_exists = _check_table_exists(cursor, "CSYSUser")

    errors: List[str] = []
    warnings: List[str] = []

    if existing_tables:
        errors.append(
            "As seguintes tabelas já existem na base e a execução foi bloqueada para evitar conflitos: "
            + ", ".join(existing_tables)
        )

    if not procedure_exists:
        errors.append("A stored procedure sp_sys_create_ID_Relation não existe na base atual.")

    if not csysuser_exists:
        warnings.append("A tabela CSYSUser não foi encontrada. As relações creationuserid/modificationuserid podem falhar.")

    return {
        "success": len(errors) == 0,
        "existing_tables": existing_tables,
        "procedure_sp_sys_create_ID_Relation_exists": procedure_exists,
        "csysuser_exists": csysuser_exists,
        "errors": errors,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------
# Execução real
# ---------------------------------------------------------------------

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

    try:
        server, port, database, username, password = get_connection_parts()
    except Exception as exc:
        return {
            "success": False,
            "mode": "blocked",
            "will_execute": False,
            "executed": False,
            "error": str(exc),
            "plan": plan,
        }

    try:
        import pymssql
    except ImportError:
        return {
            "success": False,
            "mode": "blocked",
            "will_execute": False,
            "executed": False,
            "error": (
                "pymssql não está instalado neste ambiente Python. "
                "Instala com: python3 -m pip install --user --break-system-packages pymssql"
            ),
            "plan": plan,
        }

    executable_statements = plan.get("executable_statements", [])
    execution_log: List[Dict[str, Any]] = []

    logger.info("A iniciar execução SQL Server via pymssql: %d statements", len(executable_statements))

    conn = None

    try:
        conn = _connect_pymssql()
        cursor = conn.cursor()

        preflight = run_database_preflight(cursor, plan)

        if not preflight.get("success"):
            existing_tables = preflight.get("existing_tables", []) or []
            preflight_errors = preflight.get("errors", []) or []

            message_lines = [
                "Execução bloqueada antes de alterar a base de dados."
            ]

            if existing_tables:
                message_lines.append(
                    "Tabelas já existentes: " + ", ".join(existing_tables)
                )

            if preflight_errors:
                message_lines.append(
                    "Motivo: " + " ".join(preflight_errors)
                )

            return {
                "success": False,
                "mode": "preflight_blocked",
                "will_execute": False,
                "executed": False,
                "message": "\n".join(message_lines),
                "error": " ".join(preflight_errors),
                "existing_tables": existing_tables,
                "preflight": preflight,
                "summary": plan.get("summary", {}),
                "warnings": plan.get("warnings", []) + preflight.get("warnings", []),
            }

        conn.autocommit(True)

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
        logger.exception("Erro durante execução SQL Server via pymssql")

        return {
            "success": False,
            "mode": "execute",
            "will_execute": True,
            "executed": False,
            "message": (
                "Execução falhou. Como esta versão usa autocommit para ser compatível "
                "com sp_sys_create_ID_Relation, confirma no SQL Server se alguma tabela "
                "foi criada antes do erro."
            ),
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
