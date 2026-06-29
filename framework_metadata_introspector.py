# ===== framework_metadata_introspector.py =====
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


# ---------------------------------------------------------------------
# IDs fixos confirmados na framework Demo_AIBC_Demo4
# ---------------------------------------------------------------------

FRAMEWORK_REFERENCE_IDS = {
    "application_tab_auxiliares": "E5F41C8F-5DF0-4955-BCBC-4FD21DD10469",
    "role_designer": "0A1958E3-5D3A-44DF-B9D4-3EDBD680BB94",
    "role_adm_tab_auxiliares": "8A85CE1D-A80B-479A-B417-D66C9BD48467",
    "base_object_utilizador_base": "28495E60-D823-4BC6-AF46-2ED4A54E2A2E",
    "base_object_listagem": "7B4FF922-86F4-457B-8E4C-FF5DC82CDC18",
}


# ---------------------------------------------------------------------
# .env loader simples, sem dependências externas
# ---------------------------------------------------------------------

def load_local_env(env_path: str = ".env") -> None:
    """
    Carrega variáveis de ambiente a partir de um ficheiro .env simples.
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

        if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]

        os.environ.setdefault(key, value)


def _clean_env(name: str, default: str = "") -> str:
    return str(os.getenv(name, default) or "").strip()


def get_connection_parts() -> Tuple[str, int, str, str, str]:
    load_local_env(".env")

    server = _clean_env("SQLSERVER_SERVER")
    port_raw = _clean_env("SQLSERVER_PORT", "1433")
    database = _clean_env("SQLSERVER_DATABASE")
    username = _clean_env("SQLSERVER_USERNAME")
    password = _clean_env("SQLSERVER_PASSWORD")

    missing: List[str] = []

    if not server:
        missing.append("SQLSERVER_SERVER")
    if not database:
        missing.append("SQLSERVER_DATABASE")
    if not username:
        missing.append("SQLSERVER_USERNAME")
    if not password:
        missing.append("SQLSERVER_PASSWORD")

    if missing:
        raise ValueError("Configuração SQL Server incompleta. Faltam: " + ", ".join(missing))

    try:
        port = int(port_raw)
    except ValueError as exc:
        raise ValueError(f"SQLSERVER_PORT inválido: {port_raw!r}") from exc

    return server, port, database, username, password


def connect_pymssql():
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


# ---------------------------------------------------------------------
# Serialização / helpers SQL
# ---------------------------------------------------------------------

def _json_safe(value: Any) -> Any:
    if value is None:
        return None

    if isinstance(value, (datetime, date)):
        return value.isoformat()

    if isinstance(value, Decimal):
        return float(value)

    if isinstance(value, (str, int, float, bool)):
        return value

    return str(value)


def rows_to_dicts(cursor) -> List[Dict[str, Any]]:
    columns = [col[0] for col in cursor.description or []]
    rows = cursor.fetchall()

    result: List[Dict[str, Any]] = []
    for row in rows:
        item: Dict[str, Any] = {}
        for index, column in enumerate(columns):
            item[column] = _json_safe(row[index])
        result.append(item)

    return result


def fetch_all(cursor, sql: str, params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
    cursor.execute(sql, tuple(params))
    return rows_to_dicts(cursor)


def fetch_one(cursor, sql: str, params: Sequence[Any] = ()) -> Optional[Dict[str, Any]]:
    rows = fetch_all(cursor, sql, params)
    return rows[0] if rows else None


def _ids_from(rows: Iterable[Dict[str, Any]], key: str) -> List[str]:
    seen = set()
    values: List[str] = []

    for row in rows:
        value = row.get(key)
        if value is None:
            continue
        value = str(value)
        if value not in seen:
            seen.add(value)
            values.append(value)

    return values


def _in_clause(values: Sequence[str]) -> Tuple[str, Tuple[str, ...]]:
    clean = tuple(str(v) for v in values if v)
    if not clean:
        return "(NULL)", tuple()
    return "(" + ",".join(["%s"] * len(clean)) + ")", clean


# ---------------------------------------------------------------------
# Introspection principal
# ---------------------------------------------------------------------

def get_framework_reference_constants(cursor) -> Dict[str, Any]:
    """
    Lê os IDs fixos da framework e compara com os IDs confirmados manualmente.
    """
    rows = fetch_all(
        cursor,
        """
        SELECT 'APPLICATION' AS kind, applicationid AS id, name
        FROM CSYSApplication
        WHERE name IN ('Tab. Auxiliares')

        UNION ALL

        SELECT 'ROLE' AS kind, roleid AS id, name
        FROM CSYSRole
        WHERE name IN ('Adm Tab. Auxiliares', 'Designer')

        UNION ALL

        SELECT 'BASE_OBJECT' AS kind, objectid AS id, name
        FROM CSYSObject
        WHERE name IN ('Listagem', 'Utilizador (base)')
        ORDER BY kind, name
        """,
    )

    return {
        "expected_ids": FRAMEWORK_REFERENCE_IDS,
        "database_rows": rows,
    }


def find_framework_object(
    cursor,
    *,
    object_name: Optional[str] = None,
    table_name: Optional[str] = None,
    object_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Encontra o objeto lógico em CSYSObject.
    Prioridade:
    1. object_id exato
    2. table_name em nameunc
    3. object_name em name
    """
    if object_id:
        return fetch_one(
            cursor,
            """
            SELECT
                objectid,
                name,
                description,
                nameunc,
                databasenameunc,
                pkfieldname,
                status,
                state,
                ostate,
                locked,
                descendantlocked,
                issystem,
                ishiden,
                type,
                ispublished,
                iscompile,
                defaultvalue,
                baseapplicationid,
                hidemenu,
                hastranslationdata,
                creationdate,
                modificationdate
            FROM CSYSObject
            WHERE objectid = %s
            """,
            (object_id,),
        )

    if table_name:
        row = fetch_one(
            cursor,
            """
            SELECT
                objectid,
                name,
                description,
                nameunc,
                databasenameunc,
                pkfieldname,
                status,
                state,
                ostate,
                locked,
                descendantlocked,
                issystem,
                ishiden,
                type,
                ispublished,
                iscompile,
                defaultvalue,
                baseapplicationid,
                hidemenu,
                hastranslationdata,
                creationdate,
                modificationdate
            FROM CSYSObject
            WHERE nameunc = %s
            """,
            (table_name,),
        )
        if row:
            return row

    if object_name:
        return fetch_one(
            cursor,
            """
            SELECT
                objectid,
                name,
                description,
                nameunc,
                databasenameunc,
                pkfieldname,
                status,
                state,
                ostate,
                locked,
                descendantlocked,
                issystem,
                ishiden,
                type,
                ispublished,
                iscompile,
                defaultvalue,
                baseapplicationid,
                hidemenu,
                hastranslationdata,
                creationdate,
                modificationdate
            FROM CSYSObject
            WHERE name = %s
               OR name LIKE %s
            ORDER BY CASE WHEN name = %s THEN 0 ELSE 1 END, name
            """,
            (object_name, f"%{object_name}%", object_name),
        )

    return None


def fetch_object_fields(cursor, object_id: str) -> List[Dict[str, Any]]:
    return fetch_all(
        cursor,
        """
        SELECT
            fieldid,
            objectid,
            name,
            nameunc,
            isprimarykey,
            datatype,
            systemdatatype,
            displaytype,
            fieldorder,
            maxlength,
            precision,
            scale,
            isnullable,
            status,
            state,
            ostate,
            hastranslationdata
        FROM CSYSObjectField
        WHERE objectid = %s
        ORDER BY fieldorder, nameunc
        """,
        (object_id,),
    )


def fetch_object_references(cursor, object_id: str) -> List[Dict[str, Any]]:
    return fetch_all(
        cursor,
        """
        SELECT
            r.fieldid,
            r.objectid,
            r.referencedobjectid,
            r.name,
            r.nameunc,
            r.isprimarykey,
            r.referencedtype,
            r.isnullable,
            r.relationtype,
            r.constraintname,
            r.status,
            r.state,
            r.ostate,
            o.name AS referenced_object_name,
            o.nameunc AS referenced_object_table
        FROM CSYSObjectReference r
        LEFT JOIN CSYSObject o
            ON r.referencedobjectid = o.objectid
        WHERE r.objectid = %s
        ORDER BY r.nameunc
        """,
        (object_id,),
    )


def fetch_object_layouts(cursor, object_id: str) -> List[Dict[str, Any]]:
    return fetch_all(
        cursor,
        """
        SELECT
            objectlayoutid,
            objectid,
            name,
            description,
            type,
            isdefault,
            status,
            state,
            ostate,
            isprefetch,
            layoutkey,
            version,
            workingdata,
            serverdata,
            creationdate,
            modificationdate
        FROM CSYSObjectLayout
        WHERE objectid = %s
        ORDER BY isdefault DESC, name
        """,
        (object_id,),
    )


def fetch_layout_sections(cursor, layout_ids: Sequence[str]) -> List[Dict[str, Any]]:
    clause, params = _in_clause(layout_ids)
    if not params:
        return []

    return fetch_all(
        cursor,
        f"""
        SELECT
            objectlayoutsectionid,
            objectlayoutid,
            name,
            description,
            type,
            keyname,
            visible,
            oderposition,
            isreferencecontainer,
            issubsection,
            data,
            workingdata,
            status,
            state,
            ostate,
            creationdate,
            modificationdate
        FROM CSYSObjectLayoutSection
        WHERE objectlayoutid IN {clause}
        ORDER BY objectlayoutid, oderposition, name
        """,
        params,
    )


def fetch_layout_permissions(cursor, layout_ids: Sequence[str]) -> List[Dict[str, Any]]:
    clause, params = _in_clause(layout_ids)
    if not params:
        return []

    return fetch_all(
        cursor,
        f"""
        SELECT
            lp.objectlayoutpermissionid,
            lp.name,
            lp.objectlayoutid,
            l.name AS layout_name,
            lp.roleid,
            r.name AS role_name,
            lp.applicationid,
            app.name AS application_name,
            lp.businessunitid,
            lp.workflowid,
            wf.name AS workflow_name,
            lp.workflowstateid,
            ws.name AS workflowstate_name,
            lp.isnew,
            lp.isreadonly,
            lp.oderposition,
            lp.status,
            lp.state,
            lp.ostate
        FROM CSYSObjectLayoutPermission lp
        LEFT JOIN CSYSObjectLayout l
            ON lp.objectlayoutid = l.objectlayoutid
        LEFT JOIN CSYSRole r
            ON lp.roleid = r.roleid
        LEFT JOIN CSYSApplication app
            ON lp.applicationid = app.applicationid
        LEFT JOIN CSYSWorkflow wf
            ON lp.workflowid = wf.workflowid
        LEFT JOIN CSYSWorkflowState ws
            ON lp.workflowstateid = ws.workflowstateid
        WHERE lp.objectlayoutid IN {clause}
        ORDER BY lp.oderposition, role_name
        """,
        params,
    )


def fetch_views(cursor, object_id: str, object_name: str) -> List[Dict[str, Any]]:
    return fetch_all(
        cursor,
        """
        SELECT
            viewid,
            name,
            description,
            viewtype,
            refobjectid,
            groupname,
            command,
            serverdata,
            clientdata,
            compileddate,
            autorefresh,
            editmode,
            newactionkeyname,
            hidemenu,
            enableuserstate,
            displaytype,
            status,
            state,
            ostate,
            creationdate,
            modificationdate
        FROM CSYSView
        WHERE refobjectid = %s
           OR name LIKE %s
        ORDER BY name
        """,
        (object_id, f"%{object_name}%"),
    )


def fetch_actions_and_object_actions(
    cursor,
    *,
    object_id: str,
    object_name: str,
    view_ids: Sequence[str],
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Vai buscar ações globais e ligações CSYSObjectAction relevantes.

    Nota importante:
    - Não filtramos por objectid = "Listagem" de forma isolada, porque esse objeto base
      é usado por muitas ações da framework e isso traz centenas de linhas irrelevantes.
    - Para encontrar a ligação correta "Novo <Objeto>" que aponta para o objeto base
      "Listagem", basta filtrar pelo actionid das ações globais do próprio objeto.
    """
    action_rows = fetch_all(
        cursor,
        """
        SELECT
            actionid,
            name,
            description,
            type,
            objectaction,
            baseobjectid,
            objectid,
            showtype,
            command,
            paramoptions,
            workingdata,
            serverdata,
            clientdata,
            compileddate,
            status,
            state,
            ostate,
            creationdate,
            modificationdate
        FROM CSYSAction
        WHERE name LIKE %s
           OR baseobjectid = %s
           OR objectid IN (
                SELECT viewid
                FROM CSYSView
                WHERE refobjectid = %s
           )
        ORDER BY name
        """,
        (f"%{object_name}%", object_id, object_id),
    )

    action_ids = _ids_from(action_rows, "actionid")
    context_ids = [object_id, *view_ids]

    action_clause, action_params = _in_clause(action_ids)
    context_clause, context_params = _in_clause(context_ids)

    object_actions: List[Dict[str, Any]] = []

    conditions: List[str] = []
    params: List[Any] = []

    if action_params:
        conditions.append(f"oa.actionid IN {action_clause}")
        params.extend(action_params)

    if context_params:
        conditions.append(f"oa.objectid IN {context_clause}")
        params.extend(context_params)

    conditions.append("(oa.name LIKE %s OR a.name LIKE %s)")
    params.extend([f"%{object_name}%", f"%{object_name}%"])

    where_sql = " OR ".join(conditions)

    object_actions = fetch_all(
        cursor,
        f"""
        SELECT
            oa.objectactionid,
            oa.name AS objectaction_name,
            oa.actionid,
            a.name AS action_name,
            a.type AS action_type,
            a.objectaction AS action_objectaction,
            a.baseobjectid,
            a.objectid AS action_objectid,
            oa.objectid AS objectaction_objectid,
            obj.name AS linked_object_name,
            obj.nameunc AS linked_object_table,
            v.name AS linked_view_name,
            oa.objectworkflowid,
            oa.objectworkflowstateid,
            oa.showtype,
            oa.showisnew,
            oa.showhasreadaccess,
            oa.showhaswriteaccess,
            oa.showalways,
            oa.hideaction,
            oa.musthaverecord,
            oa.forcerefresh,
            oa.parentclose,
            oa.savefirst,
            oa.autoexecute,
            oa.status,
            oa.state,
            oa.ostate
        FROM CSYSObjectAction oa
        LEFT JOIN CSYSAction a
            ON oa.actionid = a.actionid
        LEFT JOIN CSYSObject obj
            ON oa.objectid = obj.objectid
        LEFT JOIN CSYSView v
            ON oa.objectid = v.viewid
        WHERE {where_sql}
        ORDER BY linked_object_name, linked_view_name, action_name, objectaction_name
        """,
        tuple(params),
    )

    return {
        "actions": action_rows,
        "object_actions": object_actions,
    }


def fetch_permissions(
    cursor,
    *,
    object_id: str,
    view_ids: Sequence[str],
    action_ids: Sequence[str],
) -> List[Dict[str, Any]]:
    base_ids = [object_id, *view_ids, *action_ids]
    clause, params = _in_clause(base_ids)
    if not params:
        return []

    return fetch_all(
        cursor,
        f"""
        SELECT
            p.permissionid,
            p.name AS permission_name,
            p.type AS permission_type,
            p.baseobjectid,
            app.name AS application_name,
            rp.rolepermissionid,
            r.name AS role_name,
            rp.ownerpermissionmask,
            rp.bupermissionmask,
            rp.budescpermissionmask,
            rp.fullpermissionmask,
            rp.workflowid,
            wf.name AS workflow_name,
            rp.workflowstateid,
            ws.name AS workflowstate_name,
            p.status AS permission_status,
            p.state AS permission_state,
            p.ostate AS permission_ostate,
            rp.status AS rolepermission_status,
            rp.state AS rolepermission_state,
            rp.ostate AS rolepermission_ostate
        FROM CSYSPermission p
        LEFT JOIN CSYSApplication app
            ON p.applicationid = app.applicationid
        LEFT JOIN CSYSRolePermission rp
            ON p.permissionid = rp.permissionid
        LEFT JOIN CSYSRole r
            ON rp.roleid = r.roleid
        LEFT JOIN CSYSWorkflow wf
            ON rp.workflowid = wf.workflowid
        LEFT JOIN CSYSWorkflowState ws
            ON rp.workflowstateid = ws.workflowstateid
        WHERE p.baseobjectid IN {clause}
        ORDER BY p.type, p.name, role_name
        """,
        params,
    )


def build_framework_template_summary(result: Dict[str, Any]) -> Dict[str, Any]:
    """
    Gera um resumo técnico do que o futuro agente terá de reproduzir.
    """
    obj = result.get("object") or {}
    fields = result.get("fields") or []
    references = result.get("references") or []
    views = result.get("views") or []
    actions = result.get("actions") or []
    permissions = result.get("permissions") or []
    layout_permissions = result.get("layout_permissions") or []

    editable_layout_fields = ["descendantlocked", "description", "name"]

    field_names = [
        field.get("nameunc")
        for field in fields
        if field.get("nameunc")
    ]

    reference_names = [
        reference.get("nameunc")
        for reference in references
        if reference.get("nameunc")
    ]

    all_layout_field_names = []
    seen_layout_field_names = set()
    for value in [*field_names, *reference_names]:
        if value not in seen_layout_field_names:
            seen_layout_field_names.add(value)
            all_layout_field_names.append(value)

    return {
        "object": {
            "name": obj.get("name"),
            "table": obj.get("nameunc"),
            "pkfieldname": obj.get("pkfieldname"),
            "defaultvalue": obj.get("defaultvalue"),
        },
        "counts": {
            "fields": len(fields),
            "references": len(references),
            "layouts": len(result.get("layouts") or []),
            "layout_sections": len(result.get("layout_sections") or []),
            "layout_permissions": len(layout_permissions),
            "views": len(views),
            "actions": len(actions),
            "object_actions": len(result.get("object_actions") or []),
            "permissions": len(permissions),
        },
        "layout_rules": {
            "editable_fields": editable_layout_fields,
            "readonly_fields": [
                name
                for name in all_layout_field_names
                if name not in editable_layout_fields
            ],
        },
        "view_rules": {
            "visible_columns": ["name", "description"],
            "keyfield": obj.get("pkfieldname"),
            "server_filter": "where t01.state='Active'",
            "from_with_nolock": True,
        },
        "permission_rules_confirmed": {
            "object": {
                "Adm Tab. Auxiliares": 63,
                "Designer": 8192,
            },
            "view": {
                "Adm Tab. Auxiliares": 1,
                "Designer": 8192,
            },
            "action": {
                "Adm Tab. Auxiliares": 1,
            },
            "layout": {
                "Adm Tab. Auxiliares": {
                    "isnew": 1,
                    "oderposition": 10,
                }
            },
        },
    }


def introspect_framework_object(
    *,
    object_name: Optional[str] = None,
    table_name: Optional[str] = None,
    object_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Lê toda a metadata relevante de um objeto da framework.
    Não altera a base de dados.
    """
    if not object_name and not table_name and not object_id:
        raise ValueError("Indica object_name, table_name ou object_id.")

    conn = connect_pymssql()

    try:
        cursor = conn.cursor()

        obj = find_framework_object(
            cursor,
            object_name=object_name,
            table_name=table_name,
            object_id=object_id,
        )

        if not obj:
            return {
                "success": False,
                "mode": "read_only_introspection",
                "error": "Objeto não encontrado em CSYSObject.",
                "input": {
                    "object_name": object_name,
                    "table_name": table_name,
                    "object_id": object_id,
                },
            }

        resolved_object_id = str(obj["objectid"])
        resolved_object_name = str(obj.get("name") or object_name or "")

        fields = fetch_object_fields(cursor, resolved_object_id)
        references = fetch_object_references(cursor, resolved_object_id)
        layouts = fetch_object_layouts(cursor, resolved_object_id)
        layout_ids = _ids_from(layouts, "objectlayoutid")
        layout_sections = fetch_layout_sections(cursor, layout_ids)
        layout_permissions = fetch_layout_permissions(cursor, layout_ids)
        views = fetch_views(cursor, resolved_object_id, resolved_object_name)
        view_ids = _ids_from(views, "viewid")

        action_bundle = fetch_actions_and_object_actions(
            cursor,
            object_id=resolved_object_id,
            object_name=resolved_object_name,
            view_ids=view_ids,
        )
        actions = action_bundle["actions"]
        object_actions = action_bundle["object_actions"]
        action_ids = _ids_from(actions, "actionid")

        permissions = fetch_permissions(
            cursor,
            object_id=resolved_object_id,
            view_ids=view_ids,
            action_ids=action_ids,
        )

        result: Dict[str, Any] = {
            "success": True,
            "mode": "read_only_introspection",
            "warning": "Este introspector só executa SELECTs. Não cria nem altera metadata.",
            "input": {
                "object_name": object_name,
                "table_name": table_name,
                "object_id": object_id,
            },
            "framework_reference_constants": get_framework_reference_constants(cursor),
            "object": obj,
            "fields": fields,
            "references": references,
            "layouts": layouts,
            "layout_sections": layout_sections,
            "layout_permissions": layout_permissions,
            "views": views,
            "actions": actions,
            "object_actions": object_actions,
            "permissions": permissions,
        }

        result["template_summary"] = build_framework_template_summary(result)
        return result

    finally:
        try:
            conn.close()
        except Exception:
            pass


# ---------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------

def main(argv: Sequence[str]) -> int:
    if len(argv) < 2:
        print("Uso: python3 framework_metadata_introspector.py \"Teste Alpha\"")
        print("Ou:  python3 framework_metadata_introspector.py --table CUTesteAibcalpha")
        print("Ou:  python3 framework_metadata_introspector.py --id D142347A-644D-44A4-AA17-55EDF0F587C5")
        return 2

    object_name: Optional[str] = None
    table_name: Optional[str] = None
    object_id: Optional[str] = None

    if argv[1] == "--table" and len(argv) >= 3:
        table_name = argv[2]
    elif argv[1] == "--id" and len(argv) >= 3:
        object_id = argv[2]
    else:
        object_name = argv[1]

    result = introspect_framework_object(
        object_name=object_name,
        table_name=table_name,
        object_id=object_id,
    )

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
