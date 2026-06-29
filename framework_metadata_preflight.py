# ===== framework_metadata_preflight.py =====

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from framework_object_planner import (
    FRAMEWORK_REFERENCE_IDS,
    plan_framework_metadata_from_blueprint,
)


logger = logging.getLogger("framework_metadata_preflight")
logging.basicConfig(level=logging.INFO, format="[%(name)s] %(levelname)s — %(message)s")


SQLSERVER_SERVER_ENV = "SQLSERVER_SERVER"
SQLSERVER_PORT_ENV = "SQLSERVER_PORT"
SQLSERVER_DATABASE_ENV = "SQLSERVER_DATABASE"
SQLSERVER_USERNAME_ENV = "SQLSERVER_USERNAME"
SQLSERVER_PASSWORD_ENV = "SQLSERVER_PASSWORD"


# ---------------------------------------------------------------------
# .env / ligação
# ---------------------------------------------------------------------

def load_local_env(env_path: str = ".env") -> None:
    """
    Carrega variáveis de ambiente a partir de um .env simples.
    Não sobrescreve variáveis já existentes.
    """
    path = Path(env_path)

    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()

        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()

        if not key:
            continue

        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]

        os.environ.setdefault(key, value)


def _clean_env(name: str, default: str = "") -> str:
    return str(os.getenv(name, default) or "").strip()


def get_connection_parts() -> Tuple[str, int, str, str, str]:
    """
    Devolve os campos necessários para pymssql.connect().
    """
    load_local_env(".env")

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
        raise RuntimeError(
            "Configuração SQL Server incompleta. Variáveis em falta: "
            + ", ".join(missing)
        )

    try:
        port = int(port_raw)
    except ValueError as exc:
        raise RuntimeError(f"SQLSERVER_PORT inválido: {port_raw!r}") from exc

    return server, port, database, username, password


def connect_pymssql():
    """
    Abre ligação pymssql em modo leitura.
    """
    import pymssql

    server, port, database, username, password = get_connection_parts()

    return pymssql.connect(
        server=server,
        port=port,
        user=username,
        password=password,
        database=database,
        login_timeout=10,
        timeout=20,
    )


# ---------------------------------------------------------------------
# SQL helpers
# ---------------------------------------------------------------------

def fetch_all(cursor, sql: str, params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
    cursor.execute(sql, tuple(params))
    columns = [column[0] for column in cursor.description]
    rows = []

    for row in cursor.fetchall():
        rows.append(
            {
                columns[index]: value
                for index, value in enumerate(row)
            }
        )

    return rows


def fetch_one(cursor, sql: str, params: Sequence[Any] = ()) -> Optional[Dict[str, Any]]:
    rows = fetch_all(cursor, sql, params)
    return rows[0] if rows else None


def exists(cursor, sql: str, params: Sequence[Any] = ()) -> bool:
    row = fetch_one(cursor, sql, params)
    if not row:
        return False

    first_value = next(iter(row.values()))
    return bool(first_value)


def normalize_guid(value: Any) -> str:
    return str(value or "").strip().upper()


# ---------------------------------------------------------------------
# Validações de referência fixa
# ---------------------------------------------------------------------

def check_required_reference_ids(cursor) -> Dict[str, Any]:
    """
    Confirma que os IDs fixos usados pelo planner existem na framework.
    """
    checks = [
        (
            "application_tab_auxiliares",
            "CSYSApplication",
            "applicationid",
            "name",
            FRAMEWORK_REFERENCE_IDS["application_tab_auxiliares"],
            "Tab. Auxiliares",
        ),
        (
            "role_designer",
            "CSYSRole",
            "roleid",
            "name",
            FRAMEWORK_REFERENCE_IDS["role_designer"],
            "Designer",
        ),
        (
            "role_adm_tab_auxiliares",
            "CSYSRole",
            "roleid",
            "name",
            FRAMEWORK_REFERENCE_IDS["role_adm_tab_auxiliares"],
            "Adm Tab. Auxiliares",
        ),
        (
            "base_object_utilizador_base",
            "CSYSObject",
            "objectid",
            "name",
            FRAMEWORK_REFERENCE_IDS["base_object_utilizador_base"],
            "Utilizador (base)",
        ),
        (
            "base_object_listagem",
            "CSYSObject",
            "objectid",
            "name",
            FRAMEWORK_REFERENCE_IDS["base_object_listagem"],
            "Listagem",
        ),
    ]

    details = []
    missing = []

    for key, table, id_column, name_column, expected_id, expected_name in checks:
        row = fetch_one(
            cursor,
            f"""
            SELECT {id_column} AS id, {name_column} AS name
            FROM {table}
            WHERE {id_column} = %s
            """,
            (expected_id,),
        )

        ok = bool(row)
        actual_name = row.get("name") if row else None

        details.append(
            {
                "key": key,
                "table": table,
                "id_column": id_column,
                "expected_id": expected_id,
                "expected_name": expected_name,
                "found": ok,
                "actual_name": actual_name,
            }
        )

        if not ok:
            missing.append(f"ID fixo não encontrado: {key} = {expected_id} em {table}")

    return {
        "success": len(missing) == 0,
        "missing": missing,
        "details": details,
    }


# ---------------------------------------------------------------------
# Validações da tabela física
# ---------------------------------------------------------------------

def get_physical_table_columns(cursor, table_name: str) -> List[Dict[str, Any]]:
    return fetch_all(
        cursor,
        """
        SELECT
            TABLE_SCHEMA,
            TABLE_NAME,
            COLUMN_NAME,
            DATA_TYPE,
            CHARACTER_MAXIMUM_LENGTH,
            NUMERIC_PRECISION,
            NUMERIC_SCALE,
            IS_NULLABLE,
            ORDINAL_POSITION
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_NAME = %s
        ORDER BY ORDINAL_POSITION
        """,
        (table_name,),
    )


def get_physical_primary_keys(cursor, table_name: str) -> List[str]:
    rows = fetch_all(
        cursor,
        """
        SELECT
            KU.COLUMN_NAME
        FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS TC
        JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE KU
            ON TC.CONSTRAINT_NAME = KU.CONSTRAINT_NAME
           AND TC.TABLE_SCHEMA = KU.TABLE_SCHEMA
           AND TC.TABLE_NAME = KU.TABLE_NAME
        WHERE TC.CONSTRAINT_TYPE = 'PRIMARY KEY'
          AND KU.TABLE_NAME = %s
        ORDER BY KU.ORDINAL_POSITION
        """,
        (table_name,),
    )

    return [str(row["COLUMN_NAME"]) for row in rows]


def validate_physical_table(
    cursor,
    *,
    table_name: str,
    primary_key: str,
    planned_columns: Iterable[str],
) -> Dict[str, Any]:
    columns = get_physical_table_columns(cursor, table_name)
    column_names = [str(row["COLUMN_NAME"]) for row in columns]
    column_set = set(column_names)

    pk_columns = get_physical_primary_keys(cursor, table_name)

    planned_column_set = set(planned_columns)

    missing_columns = sorted(planned_column_set - column_set)
    extra_columns = sorted(column_set - planned_column_set)

    blocking_issues = []
    warnings = []

    if not columns:
        blocking_issues.append(
            f"Tabela física não existe no SQL Server: {table_name}. "
            "Executa primeiro o Database Agent."
        )

    if columns and primary_key not in column_set:
        blocking_issues.append(
            f"Primary key planeada não existe como coluna física: {table_name}.{primary_key}"
        )

    if columns and primary_key not in pk_columns:
        blocking_issues.append(
            f"Primary key física diferente ou ausente em {table_name}. "
            f"Esperado: {primary_key}; encontrado: {pk_columns or 'nenhuma'}"
        )

    # Só bloquear por colunas em falta se a tabela existir.
    # Se a tabela ainda não existe, a mensagem principal já é suficiente.
    if columns and missing_columns:
        blocking_issues.append(
            f"Colunas planeadas em falta na tabela física {table_name}: {', '.join(missing_columns)}"
        )

    if extra_columns:
        warnings.append(
            f"Tabela física {table_name} tem colunas extra não planeadas: {', '.join(extra_columns)}"
        )

    return {
        "table_name": table_name,
        "primary_key_expected": primary_key,
        "table_exists": bool(columns),
        "physical_columns": column_names,
        "physical_primary_keys": pk_columns,
        "planned_columns": sorted(planned_column_set),
        "missing_columns": missing_columns,
        "extra_columns": extra_columns,
        "blocking_issues": blocking_issues,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------
# Validações de conflitos de metadata
# ---------------------------------------------------------------------

def collect_planned_columns(object_plan: Dict[str, Any]) -> List[str]:
    columns = []

    for operation in object_plan.get("operations") or []:
        if operation.get("operation") == "UPSERT_CSYSObjectField_BATCH":
            for field in operation.get("data") or []:
                nameunc = field.get("nameunc")
                if nameunc:
                    columns.append(str(nameunc))

        elif operation.get("operation") == "UPSERT_CSYSObjectReference_BATCH":
            for reference in operation.get("data") or []:
                nameunc = reference.get("nameunc")
                if nameunc:
                    columns.append(str(nameunc))

    seen = set()
    result = []

    for column in columns:
        if column not in seen:
            seen.add(column)
            result.append(column)

    return result


def find_existing_framework_metadata(
    cursor,
    *,
    object_name: str,
    table_name: str,
    action_new_name: str,
    action_view_name: str,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Procura metadata existente que entraria em conflito com a criação.
    """
    existing_object_by_table = fetch_all(
        cursor,
        """
        SELECT objectid, name, nameunc, pkfieldname, status, state, ostate
        FROM CSYSObject
        WHERE nameunc = %s
        """,
        (table_name,),
    )

    existing_object_by_name = fetch_all(
        cursor,
        """
        SELECT objectid, name, nameunc, pkfieldname, status, state, ostate
        FROM CSYSObject
        WHERE name = %s
        """,
        (object_name,),
    )

    existing_view_by_name = fetch_all(
        cursor,
        """
        SELECT viewid, name, viewtype, refobjectid, status, state, ostate
        FROM CSYSView
        WHERE name = %s
        """,
        (object_name,),
    )

    existing_actions = fetch_all(
        cursor,
        """
        SELECT actionid, name, type, objectaction, baseobjectid, objectid, status, state, ostate
        FROM CSYSAction
        WHERE name IN (%s, %s)
        ORDER BY name
        """,
        (action_new_name, action_view_name),
    )

    return {
        "objects_by_table": existing_object_by_table,
        "objects_by_name": existing_object_by_name,
        "views_by_name": existing_view_by_name,
        "actions_by_name": existing_actions,
    }


def build_conflict_messages(
    *,
    table_name: str,
    object_name: str,
    existing: Dict[str, List[Dict[str, Any]]],
) -> List[str]:
    issues = []

    if existing["objects_by_table"]:
        ids = ", ".join(str(row.get("objectid")) for row in existing["objects_by_table"])
        issues.append(
            f"CSYSObject já existe para a tabela {table_name}. objectid(s): {ids}"
        )

    # Se existir pelo mesmo nome mas apontar para outra tabela, também é conflito.
    for row in existing["objects_by_name"]:
        if str(row.get("nameunc") or "").lower() != table_name.lower():
            issues.append(
                f"CSYSObject já existe com o nome '{object_name}' mas aponta para "
                f"{row.get('nameunc')}."
            )

    if existing["views_by_name"]:
        ids = ", ".join(str(row.get("viewid")) for row in existing["views_by_name"])
        issues.append(
            f"CSYSView/Listagem já existe com o nome '{object_name}'. viewid(s): {ids}"
        )

    if existing["actions_by_name"]:
        names = ", ".join(str(row.get("name")) for row in existing["actions_by_name"])
        issues.append(f"CSYSAction já existe: {names}")

    return issues


# ---------------------------------------------------------------------
# Preflight principal
# ---------------------------------------------------------------------

def run_framework_metadata_preflight(blueprint: Dict[str, Any]) -> Dict[str, Any]:
    """
    Executa preflight completo:
    - Gera plano da framework.
    - Valida IDs fixos.
    - Valida tabela física/PK/colunas.
    - Procura conflitos de metadata.
    """
    if not isinstance(blueprint, dict):
        raise ValueError("blueprint deve ser um dict.")

    plan = plan_framework_metadata_from_blueprint(blueprint)

    blocking_issues: List[str] = []
    warnings: List[str] = []
    object_checks: List[Dict[str, Any]] = []

    # Warnings vindos do sql_server_schema_adapter.
    # Importante: se um campo pedido pelo utilizador foi ignorado por colisão
    # com campos fixos/PK, tratamos como bloqueio para evitar criar um objeto
    # incompleto sem o utilizador reparar.
    adapter_warnings = (
        plan.get("converted_sqlserver_summary", {}).get("warnings")
        or []
    )

    for warning in adapter_warnings:
        warning_text = str(warning)
        warnings.append(f"Adapter: {warning_text}")

        if warning_text.startswith("Campo '") and "ignorado" in warning_text:
            blocking_issues.append(
                "Campo pedido pelo utilizador foi ignorado pelo adapter: "
                + warning_text
            )

    try:
        conn = connect_pymssql()
    except Exception as exc:
        return {
            "success": False,
            "mode": "framework_metadata_preflight",
            "safe_mode": "NO_DATABASE_WRITES",
            "can_execute": False,
            "blocking_issues": [f"Não foi possível abrir ligação SQL Server: {exc}"],
            "warnings": [],
            "plan_summary": {
                "objects": len(plan.get("object_plans") or []),
            },
            "adapter_warnings": adapter_warnings,
            "reference_id_check": None,
            "object_checks": [],
            "plan": plan,
        }

    try:
        cursor = conn.cursor()

        reference_check = check_required_reference_ids(cursor)
        blocking_issues.extend(reference_check["missing"])

        for object_plan in plan.get("object_plans") or []:
            object_name = str(object_plan.get("source_object") or "")
            table_name = str(object_plan.get("table_name") or "")
            primary_key = str(object_plan.get("primary_key") or "")
            planned_columns = collect_planned_columns(object_plan)

            physical_check = validate_physical_table(
                cursor,
                table_name=table_name,
                primary_key=primary_key,
                planned_columns=planned_columns,
            )

            blocking_issues.extend(physical_check["blocking_issues"])
            warnings.extend(physical_check["warnings"])

            action_new_name = f"{object_name} - Novo"
            action_view_name = f"{object_name} - Listagem"

            existing = find_existing_framework_metadata(
                cursor,
                object_name=object_name,
                table_name=table_name,
                action_new_name=action_new_name,
                action_view_name=action_view_name,
            )

            metadata_conflicts = build_conflict_messages(
                table_name=table_name,
                object_name=object_name,
                existing=existing,
            )
            blocking_issues.extend(metadata_conflicts)

            object_checks.append(
                {
                    "object_name": object_name,
                    "table_name": table_name,
                    "primary_key": primary_key,
                    "critical_rule": {
                        "CSYSObject.nameunc": table_name,
                        "CSYSObject.pkfieldname": primary_key,
                        "must_match_physical_table": True,
                    },
                    "physical_table": physical_check,
                    "existing_framework_metadata": existing,
                    "metadata_conflicts": metadata_conflicts,
                }
            )

        # Remover duplicados preservando ordem.
        blocking_issues = list(dict.fromkeys(blocking_issues))
        warnings = list(dict.fromkeys(warnings))

        can_execute = len(blocking_issues) == 0

        return {
            "success": True,
            "mode": "framework_metadata_preflight",
            "safe_mode": "NO_DATABASE_WRITES",
            "can_execute": can_execute,
            "blocking_issues": blocking_issues,
            "warnings": warnings,
            "plan_summary": {
                "objects": len(plan.get("object_plans") or []),
                "framework_mode": plan.get("mode"),
                "framework_safe_mode": plan.get("safe_mode"),
            },
            "adapter_warnings": adapter_warnings,
            "reference_id_check": reference_check,
            "object_checks": object_checks,
            "plan": plan,
        }

    finally:
        try:
            conn.close()
        except Exception:
            pass


# ---------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------

def _load_json_file(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, dict) and isinstance(data.get("schema"), dict):
        return data["schema"]

    if isinstance(data, dict) and isinstance(data.get("data"), dict):
        return data["data"]

    if not isinstance(data, dict):
        raise ValueError("O ficheiro JSON tem de conter um objeto JSON.")

    return data


def main(argv: Sequence[str]) -> int:
    if len(argv) < 2:
        print("Uso: python3 framework_metadata_preflight.py blueprint.json")
        return 2

    blueprint = _load_json_file(argv[1])
    result = run_framework_metadata_preflight(blueprint)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))

    return 0 if result.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
