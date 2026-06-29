# ===== router.py =====
from __future__ import annotations

import re
from typing import Dict, Any

from models import parse_blueprint


_UPDATE_KEYWORDS = {
    "adiciona", "adicionar",
    "acrescenta", "acrescentar",
    "cria", "criar",
    "gera", "gerar",
    "remove", "remover",
    "apaga", "apagar",
    "elimina", "eliminar",
    "renomeia", "renomear",
    "altera", "alterar",
    "muda", "mudar",
    "atualiza", "atualizar",
    "corrige", "corrigir",
    "troca", "trocar",
    "substitui", "substituir",
    "edita", "editar",
    "modifica", "modificar",
    "mete", "coloca",
    "retira", "tirar",
    "campo", "campos", "fields", "atributo", "atributos",
    "objeto", "object",
    "tipo",
    "relação", "relacao",
    "workspace",
    "ação", "acao", "action",
}


_SYSTEM_PATTERNS = [
    r"\bcria\s+um\s+sistema\b",
    r"\bcria\s+o\s+sistema\b",
    r"\bcriar\s+um\s+sistema\b",
    r"\bgerar\s+um\s+sistema\b",
    r"\bgera\s+um\s+sistema\b",
    r"\bsistema\s+para\s+gerir\b",
    r"\bsistema\s+de\s+gest[aã]o\b",
    r"\bnovo\s+sistema\b",
    r"\bsistema\s+novo\b",
]


_OBJECT_PATTERNS = [
    r"\bcria\s+um\s+objeto\b",
    r"\bcria\s+uma\s+tabela\b",
    r"\bcria\s+o\s+objeto\b",
    r"\bcriar\s+objeto\b",
    r"\bcriar\s+um\s+objeto\b",
    r"\bgera\s+um\s+objeto\b",
    r"\bgerar\s+objeto\b",
    r"\bobjeto\s+.+?\s+com\s+(?:campo|campos)\b",
    r"\bobject\s+.+?\s+with\s+fields\b",
]


_WORKSPACE_PATTERNS = [
    r"\bcria\s+um\s+workspace\b",
    r"\bcria\s+o\s+workspace\b",
    r"\bcriar\s+workspace\b",
    r"\bcriar\s+um\s+workspace\b",
    r"\bgera\s+um\s+workspace\b",
    r"\bgerar\s+workspace\b",
    r"\bperfil\s+de\s+utilizador\b",
    r"\bperfil\s+de\s+acesso\b",
]


_RELATION_PATTERNS = [
    r"\bcria\s+(?:uma\s+)?rela[cç][aã]o\b",
    r"\bcriar\s+(?:uma\s+)?rela[cç][aã]o\b",
    r"\badiciona\s+(?:uma\s+)?rela[cç][aã]o\b",
    r"\brelaciona\b",
    r"\bliga\b",
    r"\bassocia\b",
]


_CHAT_PATTERNS = [
    r"\bo\s+que\s+é\b",
    r"\bo\s+que\s+significa\b",
    r"\bexplica\b",
    r"\bajuda-me\b",
    r"\bcomo\s+funciona\b",
    r"\bmostra-me\b",
]


def _matches_any(prompt: str, patterns: list[str]) -> bool:
    return any(re.search(pattern, prompt, re.IGNORECASE) for pattern in patterns)


def _schema_is_active(current_schema: Dict[str, Any]) -> bool:
    """
    Verifica se já existe um schema ativo.
    Usa parse_blueprint para evitar falhas se o schema vier incompleto.
    """
    if not current_schema:
        return False

    try:
        bp = parse_blueprint(current_schema)
        return (
            len(bp.objects) > 0
            or len(bp.relations) > 0
            or len(bp.workspaces) > 0
            or len(bp.actions) > 0
        )
    except Exception:
        return False


def classify_prompt(prompt: str, current_schema: Dict[str, Any]) -> str:
    """
    Decide a rota principal do pedido.
    Devolve apenas a string da rota, para compatibilidade com main.py.
    """

    prompt_clean = str(prompt or "").strip()
    prompt_lower = prompt_clean.lower()
    has_active_schema = _schema_is_active(current_schema)

    print(f"[router] prompt limpo: '{prompt_clean}'")
    print(f"[router] schema ativo: {'Sim' if has_active_schema else 'Não'}")

 
    if _matches_any(prompt_lower, _SYSTEM_PATTERNS):
        return "CREATE_SYSTEM"

    if _matches_any(prompt_lower, _OBJECT_PATTERNS):
        return "UPDATE_SCHEMA"


    if _matches_any(prompt_lower, _RELATION_PATTERNS):
        return "UPDATE_SCHEMA"

    if _matches_any(prompt_lower, _WORKSPACE_PATTERNS):
        return "UPDATE_SCHEMA"


    if has_active_schema:
        if any(kw in prompt_lower for kw in _UPDATE_KEYWORDS):
            return "UPDATE_SCHEMA"

        return "UPDATE_SCHEMA"

    if _matches_any(prompt_lower, _CHAT_PATTERNS):
        return "CHAT"

    return "CREATE_SYSTEM"


classify = classify_prompt
