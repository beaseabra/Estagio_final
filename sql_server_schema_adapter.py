# ===== sql_server_schema_adapter.py =====


from __future__ import annotations

import json
import re
import unicodedata
from copy import deepcopy
from typing import Any, Dict, List, Optional, Tuple


# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURAÇÃO PRINCIPAL
# ─────────────────────────────────────────────────────────────────────────────

TABLE_PREFIX = "CU"
SYSTEM_USER_TABLE = "CSYSUser"

BASE_FIELDS: List[Dict[str, str]] = [
    {"campo": "creationdate", "display": "Date", "datatype": "date"},
    {"campo": "descendantlocked", "display": "Boolean", "datatype": "bit"},
    {"campo": "description", "display": "String", "datatype": "nvarchar(255)"},
    {"campo": "locked", "display": "Boolean", "datatype": "bit"},
    {"campo": "modificationdate", "display": "Date", "datatype": "date"},
    {"campo": "name", "display": "String", "datatype": "nvarchar(255)"},
    {"campo": "ostate", "display": "String", "datatype": "nvarchar(255)"},
    {"campo": "state", "display": "String", "datatype": "nvarchar(255)"},
    {"campo": "status", "display": "String", "datatype": "nvarchar(255)"},
    {"campo": "creationuserid", "display": "Guid", "datatype": "uniqueidentifier"},
    {"campo": "modificationuserid", "display": "Guid", "datatype": "uniqueidentifier"},
]

BASE_FIELD_NAMES = {f["campo"].lower() for f in BASE_FIELDS}

AIBIZ_TO_SQLSERVER_TYPES: Dict[str, Dict[str, str]] = {
    "string": {"display": "String", "datatype": "nvarchar(255)"},
    "text": {"display": "String", "datatype": "nvarchar(max)"},
    "integer": {"display": "Integer", "datatype": "int"},
    "int": {"display": "Integer", "datatype": "int"},
    "float": {"display": "Decimal", "datatype": "decimal(18,2)"},
    "decimal": {"display": "Decimal", "datatype": "decimal(18,2)"},
    "boolean": {"display": "Boolean", "datatype": "bit"},
    "bool": {"display": "Boolean", "datatype": "bit"},
    "date": {"display": "Date", "datatype": "date"},
    "datetime": {"display": "Date", "datatype": "date"},
    "guid": {"display": "Guid", "datatype": "uniqueidentifier"},
    "uniqueidentifier": {"display": "Guid", "datatype": "uniqueidentifier"},
}


FULL_IDENTIFIER_TRANSLATIONS: Dict[str, str] = {
    "cliente": "customer",
    "clientes": "customers",
    "produto": "product",
    "produtos": "products",
    "encomenda": "order",
    "encomendas": "orders",
    "pedido": "order",
    "pedidos": "orders",
    "pagamento": "payment",
    "pagamentos": "payments",
    "fornecedor": "supplier",
    "fornecedores": "suppliers",
    "categoria": "category",
    "categorias": "categories",
    "utilizador": "user",
    "utilizadores": "users",
    "usuario": "user",
    "usuarios": "users",
    "turista": "tourist",
    "turistas": "tourists",
    "atividade": "activity",
    "atividades": "activities",
    "reserva": "booking",
    "reservas": "bookings",
    "hotel": "hotel",
    "hoteis": "hotels",
    "quarto": "room",
    "quartos": "rooms",
    "viagem": "trip",
    "viagens": "trips",
    "destino": "destination",
    "destinos": "destinations",
    "evento": "event",
    "eventos": "events",
    "inscricao": "registration",
    "inscrições": "registrations",
    "inscricoes": "registrations",
    "livro": "book",
    "livros": "books",
    "autor": "author",
    "autores": "authors",
    "trabalho": "work",
    "trabalhos": "works",
    "projeto": "project",
    "projetos": "projects",
    "projeto_final": "final_project",
    "consulta": "appointment",
    "consultas": "appointments",
    "paciente": "patient",
    "pacientes": "patients",
    "medico": "doctor",
    "médico": "doctor",
    "medicos": "doctors",
    "médicos": "doctors",
    "aluno": "student",
    "alunos": "students",
    "curso": "course",
    "cursos": "courses",
    "professor": "teacher",
    "professores": "teachers",

    "nome": "name",
    "email": "email",
    "telefone": "phone",
    "telemovel": "mobile_phone",
    "telemóvel": "mobile_phone",
    "morada": "address",
    "endereco": "address",
    "endereço": "address",
    "nif": "tax_number",
    "codigo": "code",
    "código": "code",
    "data": "date",
    "estado": "state",
    "status": "status",
    "descricao": "description",
    "descrição": "description",
    "observacoes": "notes",
    "observações": "notes",
    "comentario": "comment",
    "comentário": "comment",
    "comentarios": "comments",
    "comentários": "comments",
    "preco": "price",
    "preço": "price",
    "valor": "value",
    "total": "total",
    "stock": "stock",
    "quantidade": "quantity",
    "prioridade": "priority",
    "titulo": "title",
    "título": "title",
    "isbn": "isbn",
    "idade": "age",
    "genero": "gender",
    "género": "gender",
    "data_entrega": "delivery_date",
    "data_inicio": "start_date",
    "data_início": "start_date",
    "data_fim": "end_date",
    "data_nascimento": "birth_date",
    "nota_final": "final_grade",
    "orcamento": "budget",
    "orçamento": "budget",
}

TOKEN_TRANSLATIONS: Dict[str, str] = {
    "cliente": "customer",
    "produto": "product",
    "encomenda": "order",
    "pedido": "order",
    "pagamento": "payment",
    "fornecedor": "supplier",
    "categoria": "category",
    "utilizador": "user",
    "usuario": "user",
    "turista": "tourist",
    "atividade": "activity",
    "actividade": "activity",
    "reserva": "booking",
    "hotel": "hotel",
    "quarto": "room",
    "viagem": "trip",
    "destino": "destination",
    "evento": "event",
    "inscricao": "registration",
    "inscricao": "registration",
    "livro": "book",
    "autor": "author",
    "trabalho": "work",
    "projeto": "project",
    "projecto": "project",
    "final": "final",
    "consulta": "appointment",
    "paciente": "patient",
    "medico": "doctor",
    "médico": "doctor",
    "aluno": "student",
    "curso": "course",
    "professor": "teacher",
    "item": "item",
    "tipo": "type",

    "nome": "name",
    "email": "email",
    "telefone": "phone",
    "telemovel": "mobile",
    "morada": "address",
    "endereco": "address",
    "nif": "tax",
    "numero": "number",
    "número": "number",
    "codigo": "code",
    "data": "date",
    "estado": "state",
    "descricao": "description",
    "observacoes": "notes",
    "comentario": "comment",
    "preco": "price",
    "valor": "value",
    "total": "total",
    "stock": "stock",
    "quantidade": "quantity",
    "prioridade": "priority",
    "titulo": "title",
    "idade": "age",
    "genero": "gender",
    "entrega": "delivery",
    "inicio": "start",
    "fim": "end",
    "nascimento": "birth",
    "nota": "grade",
    "orcamento": "budget",
}


# ─────────────────────────────────────────────────────────────────────────────
# NORMALIZAÇÃO / TRADUÇÃO
# ─────────────────────────────────────────────────────────────────────────────

def _strip_accents(value: str) -> str:
    value = str(value or "")
    normalized = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def _split_identifier(value: str) -> List[str]:
    """
    Divide identificadores em tokens:
    'data_entrega' -> ['data', 'entrega']
    'ProjetoFinal' -> ['projeto', 'final']
    'itemtypeid' -> ['itemtypeid'] se não houver separador/camel explícito.
    """
    value = _strip_accents(str(value or "")).strip()
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    value = re.sub(r"[^A-Za-z0-9]+", "_", value)
    value = value.strip("_").lower()

    if not value:
        return []

    return [p for p in value.split("_") if p]


def _normalized_key(value: str) -> str:
    return "_".join(_split_identifier(value))


def translate_identifier(value: str) -> List[str]:
    """
    Traduz identificador PT -> EN devolvendo tokens ingleses.
    Usa tradução completa se existir; caso contrário, traduz token a token.
    """
    key = _normalized_key(value)

    if key in FULL_IDENTIFIER_TRANSLATIONS:
        return _split_identifier(FULL_IDENTIFIER_TRANSLATIONS[key])

    tokens = _split_identifier(value)
    translated = [TOKEN_TRANSLATIONS.get(t, t) for t in tokens]
    return [t for t in translated if t]


def to_pascal_case_from_tokens(tokens: List[str]) -> str:
    return "".join(t[:1].upper() + t[1:] for t in tokens if t)


def to_compact_lower_from_tokens(tokens: List[str]) -> str:
    return "".join(t.lower() for t in tokens if t)


def object_to_table_info(object_name: str) -> Dict[str, str]:
    tokens = translate_identifier(object_name)
    english_entity = to_pascal_case_from_tokens(tokens) or "Entity"
    table_name = f"{TABLE_PREFIX}{english_entity}"
    pk_name = f"{to_compact_lower_from_tokens(tokens) or 'entity'}id"

    return {
        "source_object": str(object_name),
        "english_entity": english_entity,
        "table_name": table_name,
        "primary_key": pk_name,
    }


def field_to_column_name(field_name: str) -> str:
    tokens = translate_identifier(field_name)
    return to_compact_lower_from_tokens(tokens) or "field"


# ─────────────────────────────────────────────────────────────────────────────
# CONVERSÃO DE TIPOS / FIELDS
# ─────────────────────────────────────────────────────────────────────────────

def map_field_type(aibiz_type: str) -> Dict[str, str]:
    key = str(aibiz_type or "string").strip().lower()
    return deepcopy(AIBIZ_TO_SQLSERVER_TYPES.get(key, AIBIZ_TO_SQLSERVER_TYPES["string"]))


def make_primary_key_field(pk_name: str) -> Dict[str, Any]:
    return {
        "campo": pk_name,
        "display": "Guid",
        "datatype": "uniqueidentifier",
        "primary_key": True,
        "system_field": True,
        "source": "generated_primary_key",
    }


def make_base_field(field: Dict[str, str]) -> Dict[str, Any]:
    result = deepcopy(field)
    result["system_field"] = True
    result["source"] = "base_field"
    return result


def is_probable_source_primary_key(field_name: str, source_object_name: str, generated_pk: str) -> bool:
    """
    O AiBizCore já cria automaticamente '<objeto>id'.
    No formato do orientador, a PK é gerada em inglês. Portanto ignoramos a PK antiga.
    """
    raw_norm = re.sub(r"[^a-z0-9]", "", _normalized_key(field_name).lower())
    obj_norm = re.sub(r"[^a-z0-9]", "", _normalized_key(source_object_name).lower())
    generated_norm = re.sub(r"[^a-z0-9]", "", generated_pk.lower())

    return raw_norm in {
        f"{obj_norm}id",
        generated_norm,
        "id",
    }


def add_field_once(
    fields: List[Dict[str, Any]],
    field: Dict[str, Any],
    warnings: List[str],
    table_name: str,
) -> None:
    name = str(field.get("campo", "")).lower()

    if not name:
        return

    if any(str(existing.get("campo", "")).lower() == name for existing in fields):
        warnings.append(f"Campo duplicado ignorado em {table_name}: {name}")
        return

    fields.append(field)


# ─────────────────────────────────────────────────────────────────────────────
# RELAÇÕES
# ─────────────────────────────────────────────────────────────────────────────

def make_relation_query(from_table: str, from_field: str, to_table: str) -> str:
    return f"exec sp_sys_create_ID_Relation '{from_table}','{from_field}','{to_table}', null"


def make_relation_entry(
    *,
    relation_type: str,
    from_table: str,
    from_field: str,
    to_table: str,
    source_relation: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "type": relation_type,
        "from_table": from_table,
        "from_field": from_field,
        "to_table": to_table,
        "query": make_relation_query(from_table, from_field, to_table),
        "source_relation": source_relation or None,
    }


def ensure_relation_field(
    table: Dict[str, Any],
    field_name: str,
    warnings: List[str],
) -> None:
    field = {
        "campo": field_name,
        "display": "Guid",
        "datatype": "uniqueidentifier",
        "primary_key": False,
        "system_field": False,
        "relation_field": True,
        "source": "relation",
    }

    add_field_once(table["fields"], field, warnings, table["table_name"])


# ─────────────────────────────────────────────────────────────────────────────
# SQL PREVIEW
# ─────────────────────────────────────────────────────────────────────────────

def _sql_identifier(name: str) -> str:
    safe = str(name).replace("]", "]] ").replace("[", "")
    safe = safe.replace("]] ", "]]")
    return f"[{safe}]"


def generate_create_table_sql(table: Dict[str, Any]) -> str:
    lines = []
    pk_name = table["primary_key"]

    for field in table.get("fields", []):
        col = field["campo"]
        datatype = field["datatype"]

        if col.lower() == pk_name.lower():
            lines.append(f"    {_sql_identifier(col)} {datatype} NOT NULL DEFAULT NEWID()")
        else:
            lines.append(f"    {_sql_identifier(col)} {datatype} NULL")

    lines.append(
        f"    CONSTRAINT {_sql_identifier('PK_' + table['table_name'])} PRIMARY KEY ({_sql_identifier(pk_name)})"
    )

    body = ",\n".join(lines)
    return f"CREATE TABLE {_sql_identifier(table['table_name'])} (\n{body}\n);"


def generate_sql_preview(tables: List[Dict[str, Any]], relations: List[Dict[str, Any]]) -> List[str]:
    sql: List[str] = []

    for table in tables:
        sql.append(generate_create_table_sql(table))

    if relations:
        sql.append("-- Relações criadas pela stored procedure do sistema")

    for relation in relations:
        sql.append(relation["query"])

    return sql


# ─────────────────────────────────────────────────────────────────────────────
# FUNÇÃO PRINCIPAL
# ─────────────────────────────────────────────────────────────────────────────

def convert_blueprint_to_sqlserver_schema(blueprint: Dict[str, Any]) -> Dict[str, Any]:
    """
    Converte o blueprint completo do AiBizCore para o formato de DB do orientador.

    Usa apenas:
    - objects
    - relations

    Ignora:
    - actions
    - workspaces
    """
    warnings: List[str] = []
    blueprint = blueprint if isinstance(blueprint, dict) else {}

    objects = blueprint.get("objects") or []
    relations = blueprint.get("relations") or []

    if not isinstance(objects, list):
        objects = []
        warnings.append("blueprint.objects não era lista; ignorado.")

    if not isinstance(relations, list):
        relations = []
        warnings.append("blueprint.relations não era lista; ignorado.")

    object_map: Dict[str, Dict[str, str]] = {}
    tables_by_source: Dict[str, Dict[str, Any]] = {}
    tables: List[Dict[str, Any]] = []
    generated_relations: List[Dict[str, Any]] = []

    # 1. Criar tabelas a partir de objects.
    for obj in objects:
        if not isinstance(obj, dict):
            warnings.append(f"Objeto inválido ignorado: {obj!r}")
            continue

        source_name = str(obj.get("name") or "").strip()

        if not source_name:
            warnings.append("Objeto sem nome ignorado.")
            continue

        info = object_to_table_info(source_name)
        source_key = source_name.lower()

        if source_key in tables_by_source:
            warnings.append(f"Objeto duplicado ignorado: {source_name}")
            continue

        fields: List[Dict[str, Any]] = []
        add_field_once(fields, make_primary_key_field(info["primary_key"]), warnings, info["table_name"])

        for base_field in BASE_FIELDS:
            add_field_once(fields, make_base_field(base_field), warnings, info["table_name"])

        table = {
            "source_object": source_name,
            "english_entity": info["english_entity"],
            "table_name": info["table_name"],
            "primary_key": info["primary_key"],
            "fields": fields,
        }

        # Campos específicos definidos pelo utilizador no AiBizCore.
        for field in obj.get("fields") or []:
            if not isinstance(field, dict):
                warnings.append(f"Campo inválido ignorado em {source_name}: {field!r}")
                continue

            source_field_name = str(field.get("name") or "").strip()

            if not source_field_name:
                continue

            if is_probable_source_primary_key(source_field_name, source_name, info["primary_key"]):
                warnings.append(
                    f"Primary key antiga ignorada em {info['table_name']}: {source_field_name}"
                )
                continue

            column_name = field_to_column_name(source_field_name)

            if column_name.lower() in BASE_FIELD_NAMES or column_name.lower() == info["primary_key"].lower():
                warnings.append(
                    f"Campo '{source_field_name}' ignorado em {info['table_name']} porque já existe como campo fixo/PK."
                )
                continue

            type_info = map_field_type(field.get("type", "string"))
            add_field_once(
                table["fields"],
                {
                    "campo": column_name,
                    "display": type_info["display"],
                    "datatype": type_info["datatype"],
                    "primary_key": False,
                    "system_field": False,
                    "source": "user_field",
                    "source_field": source_field_name,
                    "source_type": field.get("type", "string"),
                },
                warnings,
                info["table_name"],
            )

        tables_by_source[source_key] = table
        object_map[source_key] = info
        tables.append(table)

        # Relações obrigatórias para creationuserid e modificationuserid.
        generated_relations.append(
            make_relation_entry(
                relation_type="SYSTEM_USER",
                from_table=info["table_name"],
                from_field="creationuserid",
                to_table=SYSTEM_USER_TABLE,
            )
        )
        generated_relations.append(
            make_relation_entry(
                relation_type="SYSTEM_USER",
                from_table=info["table_name"],
                from_field="modificationuserid",
                to_table=SYSTEM_USER_TABLE,
            )
        )

    # 2. Converter relações entre objetos.
    for rel in relations:
        if not isinstance(rel, dict):
            warnings.append(f"Relação inválida ignorada: {rel!r}")
            continue

        source_obj = str(rel.get("from") or rel.get("from_obj") or "").strip()
        target_obj = str(rel.get("to") or rel.get("to_obj") or "").strip()
        rel_type = str(rel.get("type") or "ONE_TO_MANY").strip().upper()

        source_table = tables_by_source.get(source_obj.lower())
        target_table = tables_by_source.get(target_obj.lower())

        if source_table is None or target_table is None:
            warnings.append(
                f"Relação ignorada porque referencia objeto inexistente: {source_obj} -> {target_obj}"
            )
            continue

        if rel_type == "ONE_TO_MANY":
            # A tabela destino recebe o ID da tabela origem.
            fk_field = source_table["primary_key"]
            ensure_relation_field(target_table, fk_field, warnings)
            generated_relations.append(
                make_relation_entry(
                    relation_type="ONE_TO_MANY",
                    from_table=target_table["table_name"],
                    from_field=fk_field,
                    to_table=source_table["table_name"],
                    source_relation=rel,
                )
            )

        elif rel_type == "MANY_TO_MANY":
            source_fk = source_table["primary_key"]
            target_fk = target_table["primary_key"]

            ensure_relation_field(source_table, target_fk, warnings)
            ensure_relation_field(target_table, source_fk, warnings)

            generated_relations.append(
                make_relation_entry(
                    relation_type="MANY_TO_MANY",
                    from_table=source_table["table_name"],
                    from_field=target_fk,
                    to_table=target_table["table_name"],
                    source_relation=rel,
                )
            )
            generated_relations.append(
                make_relation_entry(
                    relation_type="MANY_TO_MANY",
                    from_table=target_table["table_name"],
                    from_field=source_fk,
                    to_table=source_table["table_name"],
                    source_relation=rel,
                )
            )

        else:
            warnings.append(f"Tipo de relação desconhecido ignorado: {rel_type}")

    # 3. Gerar SQL preview.
    sql_preview = generate_sql_preview(tables, generated_relations)

    return {
        "success": True,
        "database_target": "sql_server",
        "table_prefix": TABLE_PREFIX,
        "uses_only": ["objects", "relations"],
        "ignored_blueprint_sections": ["actions", "workspaces"],
        "tables": tables,
        "relations": generated_relations,
        "sql_preview": sql_preview,
        "warnings": warnings,
        "mapping": {
            "objects": {
                table["source_object"]: {
                    "table_name": table["table_name"],
                    "primary_key": table["primary_key"],
                    "english_entity": table["english_entity"],
                }
                for table in tables
            }
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# TESTE LOCAL RÁPIDO
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    sample = {
        "objects": [
            {
                "name": "Cliente",
                "fields": [
                    {"name": "clienteid", "type": "integer"},
                    {"name": "nome", "type": "string"},
                    {"name": "email", "type": "string"},
                    {"name": "telefone", "type": "string"},
                    {"name": "status", "type": "string"},
                ],
            },
            {
                "name": "Encomenda",
                "fields": [
                    {"name": "encomendaid", "type": "integer"},
                    {"name": "codigo", "type": "string"},
                    {"name": "data", "type": "date"},
                    {"name": "total", "type": "float"},
                ],
            },
        ],
        "relations": [
            {"from": "Cliente", "to": "Encomenda", "type": "ONE_TO_MANY"}
        ],
        "actions": [{"name": "ValidarEncomenda"}],
        "workspaces": [{"name": "Vendas"}],
    }

    converted = convert_blueprint_to_sqlserver_schema(sample)
    print(json.dumps(converted, ensure_ascii=False, indent=2))
