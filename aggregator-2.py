# ===== aggregator.py =====
# FIX: expõe build_final_blueprint (nome canónico) E aggregate_blueprint (alias de
# retrocompatibilidade) para eliminar o ImportError em create_system_handler e feedback_loop.

from typing import Dict, Any
from models import parse_blueprint


def build_final_blueprint(
    objects: list,
    relations: list,
    workspaces: list,
    actions: list,
) -> Dict[str, Any]:
    """
    Recebe as listas parciais dos vários geradores, junta tudo e passa
    pela armadura Pydantic para garantir que não há nulls ou chaves inválidas.
    Ordem dos parâmetros: objects, relations, workspaces, actions.
    """
    raw_blueprint = {
        "objects":    objects    if isinstance(objects, list)    else [],
        "relations":  relations  if isinstance(relations, list)  else [],
        "workspaces": workspaces if isinstance(workspaces, list) else [],
        "actions":    actions    if isinstance(actions, list)    else [],
    }

    clean_blueprint = parse_blueprint(raw_blueprint).to_dict()

    total_objs = len(clean_blueprint.get("objects", []))
    print(f"[aggregator] OK — {total_objs} objetos montados de forma segura.")
    return clean_blueprint


def aggregate_blueprint(
    objects_data:    Any,
    relations_data:  Any,
    actions_data:    Any,
    workspaces_data: Any,
) -> Dict[str, Any]:
    """
    Alias retrocompatível usado por create_system_handler e feedback_loop.

    ATENÇÃO: a ordem dos argumentos é diferente da função canónica:
        aggregate_blueprint(objects, relations, actions, workspaces)
    enquanto build_final_blueprint recebe (objects, relations, workspaces, actions).
    Este wrapper normaliza a diferença.
    """
    def _extract_list(data: Any, key: str) -> list:
        if isinstance(data, dict):
            return data.get(key, [])
        if isinstance(data, list):
            return data
        return []

    objects    = _extract_list(objects_data,    "objects")
    relations  = _extract_list(relations_data,  "relations")
    actions    = _extract_list(actions_data,    "actions")
    workspaces = _extract_list(workspaces_data, "workspaces")

    return build_final_blueprint(objects, relations, workspaces, actions)
