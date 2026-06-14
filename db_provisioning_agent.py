# ===== db_provisioning_agent.py =====
# Agente de Provisionamento de Base de Dados — AiBizCore Fase 2
#
# Transforma um schema JSON validado da AiBizCore em:
#   - DDL SQL puro (PostgreSQL / SQLite)
#   - Modelos SQLAlchemy (ORM)
#   - Execução directa na BD (modo live)
#
# Arquitectura: o agente é stateless — recebe o schema, produz artefactos.

from __future__ import annotations

import json
import re
import os
import logging
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# SQLAlchemy é opcional — o modo DDL puro funciona sempre
try:
    from sqlalchemy import (
        Column, Integer, Float, Boolean, String, Text,
        DateTime, Date, ForeignKey, Table, MetaData, create_engine
    )
    from sqlalchemy.orm import DeclarativeBase, relationship
    SQLALCHEMY_AVAILABLE = True
except ImportError:
    SQLALCHEMY_AVAILABLE = False

logger = logging.getLogger("db_provisioning_agent")
logging.basicConfig(level=logging.INFO, format="[%(name)s] %(levelname)s — %(message)s")


# ─────────────────────────────────────────────
# SECÇÃO 1 — CONFIGURAÇÃO E TIPOS
# ─────────────────────────────────────────────

class TargetDialect(str, Enum):
    POSTGRESQL = "postgresql"
    SQLITE     = "sqlite"
    MYSQL      = "mysql"


# Mapeamento: tipo AiBizCore → tipo SQL por dialecto
_SQL_TYPE_MAP: Dict[TargetDialect, Dict[str, str]] = {
    TargetDialect.POSTGRESQL: {
        "string":   "VARCHAR(255)",
        "text":     "TEXT",
        "integer":  "INTEGER",
        "float":    "NUMERIC(15, 4)",
        "boolean":  "BOOLEAN",
        "date":     "DATE",
        "datetime": "TIMESTAMP",
    },
    TargetDialect.SQLITE: {
        "string":   "TEXT",
        "text":     "TEXT",
        "integer":  "INTEGER",
        "float":    "REAL",
        "boolean":  "INTEGER",   # SQLite não tem BOOLEAN nativo
        "date":     "TEXT",
        "datetime": "TEXT",
    },
    TargetDialect.MYSQL: {
        "string":   "VARCHAR(255)",
        "text":     "TEXT",
        "integer":  "INT",
        "float":    "DECIMAL(15, 4)",
        "boolean":  "TINYINT(1)",
        "date":     "DATE",
        "datetime": "DATETIME",
    },
}

# Mapeamento: tipo AiBizCore → tipo SQLAlchemy
_SA_TYPE_MAP = {
    "string":   "String(255)",
    "text":     "Text",
    "integer":  "Integer",
    "float":    "Float",
    "boolean":  "Boolean",
    "date":     "Date",
    "datetime": "DateTime",
}


# ─────────────────────────────────────────────
# SECÇÃO 2 — UTILITÁRIOS
# ─────────────────────────────────────────────

def _to_snake(name: str) -> str:
    """Converte NomeCamelCase ou Nome Composto para snake_case."""
    name = re.sub(r"[\s\-]+", "_", name.strip())
    name = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name)
    name = re.sub(r"([a-z\d])([A-Z])", r"\1_\2", name)
    return name.lower()


def _table_name(object_name: str) -> str:
    return _to_snake(object_name)


def _pk_field_name(object_name: str) -> str:
    """Devolve o nome da PK canónica do objecto."""
    return f"{_to_snake(object_name)}_id"


def _sql_type(aibizcore_type: str, dialect: TargetDialect) -> str:
    return _SQL_TYPE_MAP[dialect].get(aibizcore_type.lower(), "TEXT")


def _is_fk_field(field_name: str) -> bool:
    return field_name.startswith("ref_")


def _fk_target_table(field_name: str) -> str:
    """ref_cliente → cliente"""
    return field_name.replace("ref_", "").lower()


# ─────────────────────────────────────────────
# SECÇÃO 3 — GERAÇÃO DE DDL SQL
# ─────────────────────────────────────────────

class SQLDDLGenerator:
    """Gera DDL SQL a partir do schema AiBizCore."""

    def __init__(self, schema: Dict, dialect: TargetDialect = TargetDialect.POSTGRESQL):
        self.schema  = schema
        self.dialect = dialect
        self._object_map: Dict[str, str] = {}   # nome → table_name
        self._build_object_map()

    def _build_object_map(self):
        for obj in self.schema.get("objects", []):
            name = obj.get("name", "")
            self._object_map[name.lower()] = _table_name(name)

    # ── Cabeçalho ──────────────────────────────────────────────
    def _header(self) -> str:
        lines = [
            f"-- ============================================",
            f"-- AiBizCore — DDL Auto-Gerado",
            f"-- Dialecto: {self.dialect.value.upper()}",
            f"-- Data: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC",
            f"-- ============================================",
            "",
        ]
        if self.dialect == TargetDialect.POSTGRESQL:
            lines += [
                "-- Extensões",
                "-- CREATE EXTENSION IF NOT EXISTS \"uuid-ossp\";",
                "",
            ]
        return "\n".join(lines)

    # ── Uma tabela ─────────────────────────────────────────────
    def _generate_table(self, obj: Dict) -> str:
        obj_name  = obj.get("name", "")
        tbl_name  = _table_name(obj_name)
        pk_name   = _pk_field_name(obj_name)

        # Colunas
        column_lines: List[str] = []

        # PK garantida
        if self.dialect == TargetDialect.POSTGRESQL:
            column_lines.append(f"    {pk_name} SERIAL PRIMARY KEY")
        elif self.dialect == TargetDialect.MYSQL:
            column_lines.append(f"    {pk_name} INT NOT NULL AUTO_INCREMENT PRIMARY KEY")
        else:
            column_lines.append(f"    {pk_name} INTEGER PRIMARY KEY AUTOINCREMENT")

        for field in obj.get("fields", []):
            fname = field.get("name", "").strip().lower()
            ftype = field.get("type", "string")

            # Saltar PK (já adicionada)
            if fname == pk_name or fname.endswith("id") and fname == f"{_to_snake(obj_name)}id":
                continue

            # FK
            if _is_fk_field(fname):
                target = _fk_target_table(fname)
                real_table = self._object_map.get(target, target)
                real_pk    = f"{target}_id"
                column_lines.append(
                    f"    {fname} INTEGER REFERENCES {real_table}({real_pk}) ON DELETE SET NULL"
                )
                continue

            # Campo normal
            sql_t   = _sql_type(ftype, self.dialect)
            not_null = " NOT NULL" if fname in ("created_at",) else ""
            default  = ""
            if fname == "created_at":
                if self.dialect == TargetDialect.POSTGRESQL:
                    default = " DEFAULT NOW()"
                elif self.dialect == TargetDialect.MYSQL:
                    default = " DEFAULT CURRENT_TIMESTAMP"
                else:
                    default = " DEFAULT CURRENT_TIMESTAMP"
            elif fname == "ativo" and ftype == "boolean":
                default = " DEFAULT TRUE" if self.dialect != TargetDialect.SQLITE else " DEFAULT 1"

            column_lines.append(f"    {fname} {sql_t}{not_null}{default}")

        cols_str = ",\n".join(column_lines)
        ddl = f"CREATE TABLE IF NOT EXISTS {tbl_name} (\n{cols_str}\n);\n"
        return ddl

    # ── Todos os índices ───────────────────────────────────────
    def _generate_indexes(self) -> str:
        lines = ["-- Índices de desempenho", ""]
        for obj in self.schema.get("objects", []):
            tbl = _table_name(obj.get("name", ""))
            for field in obj.get("fields", []):
                fname = field.get("name", "").strip().lower()
                if _is_fk_field(fname) or fname in ("created_at", "updated_at", "ativo"):
                    lines.append(
                        f"CREATE INDEX IF NOT EXISTS idx_{tbl}_{fname} ON {tbl}({fname});"
                    )
        return "\n".join(lines)

    # ── Ponto de entrada ───────────────────────────────────────
    def generate(self) -> str:
        parts = [self._header()]

        for obj in self.schema.get("objects", []):
            parts.append(f"-- Tabela: {obj.get('name', '')}")
            parts.append(self._generate_table(obj))

        parts.append(self._generate_indexes())

        return "\n".join(parts)


# ─────────────────────────────────────────────
# SECÇÃO 4 — GERAÇÃO DE MODELOS SQLALCHEMY
# ─────────────────────────────────────────────

class SQLAlchemyModelGenerator:
    """Gera código Python com modelos SQLAlchemy declarativos."""

    def __init__(self, schema: Dict):
        self.schema = schema
        self._object_names: set = {
            o.get("name", "").lower()
            for o in schema.get("objects", [])
        }

    def _field_to_column(self, field: Dict, pk_name: str, obj_name: str) -> Optional[str]:
        fname = field.get("name", "").strip().lower()
        ftype = field.get("type", "string")

        if fname == pk_name:
            return None   # PK gerada separadamente

        # FK
        if _is_fk_field(fname):
            target = _fk_target_table(fname)
            target_tbl = _table_name(target)
            target_pk  = f"{target}_id"
            return (
                f"    {fname} = Column(Integer, "
                f"ForeignKey('{target_tbl}.{target_pk}'), nullable=True)"
            )

        sa_type = _SA_TYPE_MAP.get(ftype.lower(), "String(255)")
        nullable = "False" if fname == "created_at" else "True"
        extras   = ""
        if fname == "created_at":
            extras = ", default=datetime.utcnow"
        elif fname == "updated_at":
            extras = ", default=datetime.utcnow, onupdate=datetime.utcnow"
        elif fname == "ativo":
            extras = ", default=True"

        return f"    {fname} = Column({sa_type}, nullable={nullable}{extras})"

    def _generate_model(self, obj: Dict) -> str:
        obj_name  = obj.get("name", "")
        class_name = obj_name   # já em PascalCase
        tbl_name  = _table_name(obj_name)
        pk_name   = _pk_field_name(obj_name)

        lines = [
            f"class {class_name}(Base):",
            f'    __tablename__ = "{tbl_name}"',
            f"",
            f"    {pk_name} = Column(Integer, primary_key=True, autoincrement=True)",
        ]

        for field in obj.get("fields", []):
            col_line = self._field_to_column(field, pk_name, obj_name)
            if col_line:
                lines.append(col_line)

        lines.append("")
        lines.append(f"    def __repr__(self):")
        lines.append(
            f'        return f"<{class_name} {{{pk_name}={{self.{pk_name}}}}}>\"'
        )
        lines.append("")

        return "\n".join(lines)

    def generate(self) -> str:
        header = [
            "# ===== models.py — Auto-gerado pelo AiBizCore DB Provisioning Agent =====",
            f"# Data: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC",
            "",
            "from __future__ import annotations",
            "from datetime import datetime",
            "from sqlalchemy import Column, Integer, Float, Boolean, String, Text, DateTime, Date, ForeignKey",
            "from sqlalchemy.orm import DeclarativeBase, relationship",
            "",
            "",
            "class Base(DeclarativeBase):",
            "    pass",
            "",
            "",
        ]

        model_blocks = []
        for obj in self.schema.get("objects", []):
            model_blocks.append(self._generate_model(obj))

        return "\n".join(header) + "\n".join(model_blocks)


# ─────────────────────────────────────────────
# SECÇÃO 5 — EXECUÇÃO DIRECTA (modo live)
# ─────────────────────────────────────────────

class LiveProvisioner:
    """
    Executa o DDL directamente numa base de dados via SQLAlchemy.
    Requer SQLALCHEMY_AVAILABLE = True.
    """

    def __init__(self, connection_url: str, schema: Dict, dialect: TargetDialect):
        if not SQLALCHEMY_AVAILABLE:
            raise RuntimeError("sqlalchemy não está instalado. Executa: pip install sqlalchemy")
        self.connection_url = connection_url
        self.schema = schema
        self.dialect = dialect

    def provision(self) -> Dict[str, Any]:
        """Cria todas as tabelas. Idempotente (IF NOT EXISTS)."""
        generator = SQLDDLGenerator(self.schema, self.dialect)
        ddl_script = generator.generate()

        engine = create_engine(self.connection_url, echo=False)
        results = []

        with engine.connect() as conn:
            # Executar statement por statement para relatório detalhado
            statements = [
                s.strip() for s in ddl_script.split(";")
                if s.strip() and not s.strip().startswith("--")
            ]
            for stmt in statements:
                try:
                    conn.execute(stmt + ";")  # type: ignore
                    results.append({"sql": stmt[:80] + "...", "status": "OK"})
                except Exception as e:
                    results.append({"sql": stmt[:80] + "...", "status": "ERROR", "error": str(e)})
            conn.commit()

        success_count = sum(1 for r in results if r["status"] == "OK")
        error_count   = len(results) - success_count

        return {
            "success": error_count == 0,
            "statements_executed": len(results),
            "success_count": success_count,
            "error_count": error_count,
            "detail": results
        }


# ─────────────────────────────────────────────
# SECÇÃO 6 — VALIDAÇÃO PRÉ-PROVISIONAMENTO
# ─────────────────────────────────────────────

def _validate_schema_for_db(schema: Dict) -> Tuple[bool, List[str]]:
    """
    Verificações mínimas antes de provisionar.
    Devolve (is_valid, lista_de_erros).
    """
    errors: List[str] = []

    objects = schema.get("objects", [])
    if not objects:
        errors.append("Schema não tem objectos/tabelas.")
        return False, errors

    object_names_lower = {_to_snake(o.get("name", "")).lower() for o in objects}

    for obj in objects:
        name = obj.get("name", "")
        if not name:
            errors.append("Objecto sem nome encontrado.")
            continue

        fields = obj.get("fields", [])
        if not fields:
            errors.append(f"Objecto '{name}' não tem campos.")
            continue

        # Verificar FK dangling
        for field in fields:
            fname = field.get("name", "")
            if _is_fk_field(fname):
                target = _fk_target_table(fname)
                if target not in object_names_lower:
                    errors.append(
                        f"Campo '{fname}' em '{name}' aponta para tabela '{target}' que não existe no schema."
                    )

    return len(errors) == 0, errors


# ─────────────────────────────────────────────
# SECÇÃO 7 — PONTO DE ENTRADA PRINCIPAL
# ─────────────────────────────────────────────

def provision_database(
    schema: Dict,
    output_dir: str = "database/generated",
    dialect: str = "postgresql",
    connection_url: Optional[str] = None,
    dry_run: bool = True
) -> Dict[str, Any]:
    """
    Ponto de entrada do agente.

    Args:
        schema:         Schema JSON validado da AiBizCore.
        output_dir:     Directório para guardar os ficheiros gerados.
        dialect:        "postgresql" | "sqlite" | "mysql"
        connection_url: URL de ligação (necessário apenas se dry_run=False).
        dry_run:        Se True, gera ficheiros mas não toca na BD.

    Returns:
        Dicionário com resultado, caminhos dos ficheiros e erros.
    """

    # ── Validação ──────────────────────────────────────────────
    try:
        target_dialect = TargetDialect(dialect.lower())
    except ValueError:
        return {"success": False, "error": f"Dialecto inválido: '{dialect}'. Use postgresql, sqlite ou mysql."}

    is_valid, validation_errors = _validate_schema_for_db(schema)
    if not is_valid:
        return {
            "success": False,
            "error": "Schema inválido para provisionamento.",
            "validation_errors": validation_errors
        }

    # ── Geração de artefactos ──────────────────────────────────
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    generated_files: Dict[str, str] = {}

    # 1. DDL SQL
    ddl_generator = SQLDDLGenerator(schema, target_dialect)
    ddl_content   = ddl_generator.generate()
    ddl_path      = output_path / f"schema_{timestamp}.sql"
    ddl_path.write_text(ddl_content, encoding="utf-8")
    generated_files["sql_ddl"] = str(ddl_path)
    logger.info(f"DDL gerado: {ddl_path}")

    # 2. Modelos SQLAlchemy
    sa_generator = SQLAlchemyModelGenerator(schema)
    sa_content   = sa_generator.generate()
    sa_path      = output_path / f"models_{timestamp}.py"
    sa_path.write_text(sa_content, encoding="utf-8")
    generated_files["sqlalchemy_models"] = str(sa_path)
    logger.info(f"Modelos SQLAlchemy gerados: {sa_path}")

    # 3. Manifesto JSON (auditoria)
    manifest = {
        "generated_at": timestamp,
        "dialect": dialect,
        "dry_run": dry_run,
        "schema_summary": {
            "objects": len(schema.get("objects", [])),
            "relations": len(schema.get("relations", [])),
            "actions": len(schema.get("actions", [])),
            "workspaces": len(schema.get("workspaces", []))
        },
        "files": generated_files
    }
    manifest_path = output_path / f"manifest_{timestamp}.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    generated_files["manifest"] = str(manifest_path)

    result: Dict[str, Any] = {
        "success": True,
        "dry_run": dry_run,
        "dialect": dialect,
        "generated_files": generated_files,
        "objects_processed": len(schema.get("objects", []))
    }

    # ── Execução live (se solicitado) ──────────────────────────
    if not dry_run:
        if not connection_url:
            return {
                **result,
                "success": False,
                "error": "connection_url é obrigatório quando dry_run=False."
            }
        logger.info(f"Modo LIVE: a provisionar em {connection_url[:30]}...")
        provisioner = LiveProvisioner(connection_url, schema, target_dialect)
        live_result = provisioner.provision()
        result["live_provisioning"] = live_result
        result["success"] = live_result["success"]
    else:
        logger.info("Modo DRY RUN — nenhuma BD foi alterada.")

    return result


# ─────────────────────────────────────────────
# SECÇÃO 8 — INTEGRAÇÃO COM A API FASTAPI
# ─────────────────────────────────────────────
#
# Adiciona estas rotas ao teu api.py:
#
# from db_provisioning_agent import provision_database
#
# class ProvisionPayload(BaseModel):
#     schema_data: Dict[str, Any]
#     dialect: str = "postgresql"
#     dry_run: bool = True
#     connection_url: Optional[str] = None
#
# @app.post("/api/provision_db")
# def provision_db(payload: ProvisionPayload):
#     result = provision_database(
#         schema=payload.schema_data,
#         dialect=payload.dialect,
#         connection_url=payload.connection_url,
#         dry_run=payload.dry_run
#     )
#     if not result["success"]:
#         raise HTTPException(status_code=400, detail=result)
#     return result
#
# ─────────────────────────────────────────────


# ─────────────────────────────────────────────
# SECÇÃO 9 — CLI (teste rápido)
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    # Exemplo mínimo para teste sem BD real
    _sample_schema = {
        "objects": [
            {
                "name": "Cliente",
                "fields": [
                    {"name": "clienteid", "type": "integer"},
                    {"name": "nome", "type": "string"},
                    {"name": "email", "type": "string"},
                    {"name": "ativo", "type": "boolean"},
                    {"name": "created_at", "type": "datetime"}
                ]
            },
            {
                "name": "Encomenda",
                "fields": [
                    {"name": "encomendaid", "type": "integer"},
                    {"name": "ref_cliente", "type": "integer"},
                    {"name": "total", "type": "float"},
                    {"name": "estado", "type": "string"},
                    {"name": "created_at", "type": "datetime"}
                ]
            }
        ],
        "relations": [
            {"from": "Encomenda", "to": "Cliente", "type": "ONE_TO_MANY"}
        ],
        "actions": [],
        "workspaces": []
    }

    dialect_arg = sys.argv[1] if len(sys.argv) > 1 else "postgresql"

    print(f"\n🛠️  AiBizCore — DB Provisioning Agent (DRY RUN / {dialect_arg.upper()})\n")
    output = provision_database(
        schema=_sample_schema,
        dialect=dialect_arg,
        dry_run=True
    )

    print(json.dumps(output, indent=2, ensure_ascii=False))