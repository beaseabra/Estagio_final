# ===== router.py =====
# FIX: main.py faz `from router import classify` mas a função chamava-se classify_prompt.
# Expõe ambos os nomes para retrocompatibilidade.

import re
from typing import Dict, Any, Tuple
from models import parse_blueprint


# Palavras-chave que indicam claramente um desejo de alterar algo existente
_UPDATE_KEYWORDS = {
    "adiciona", "adicionar", "remove", "remover", "apaga", "apagar",
    "elimina", "eliminar", "renomeia", "renomear", "altera", "alterar",
    "muda", "mudar", "atualiza", "atualizar", "cria o", "cria a",
    "cria um novo", "cria uma nova", "acção", "ação",
}


def _schema_is_active(current_schema: Dict[str, Any]) -> bool:
    """Verifica de forma blindada se já existe um projeto ativo na cache."""
    if not current_schema:
        return False
    bp = parse_blueprint(current_schema)
    return len(bp.objects) > 0 or len(bp.workspaces) > 0


def classify_prompt(prompt: str, current_schema: Dict[str, Any]) -> str:
    """
    Analisa o prompt e o estado da cache para decidir a rota.
    Devolve apenas a string da rota (sem confiança) para compatibilidade com main.py.
    """
    prompt_lower = prompt.lower()
    has_active_schema = _schema_is_active(current_schema)

    print(f"[router] prompt limpo: '{prompt}'")
    print(f"[router] schema ativo: {'Sim' if has_active_schema else 'Não'}")

    # Heurística 1 — criação explícita de raiz
    if "cria um sistema" in prompt_lower or "cria o sistema" in prompt_lower:
        return "CREATE_SYSTEM"

    # Heurística 2 — keyword de modificação com schema ativo
    if any(kw in prompt_lower for kw in _UPDATE_KEYWORDS) and has_active_schema:
        return "UPDATE_SCHEMA"

    # Heurística 3 — schema ativo sem pedido explícito de novo sistema
    if has_active_schema:
        return "UPDATE_SCHEMA"

    # Heurística 4 — cache vazia → criar sistema novo
    return "CREATE_SYSTEM"


# FIX: alias que main.py importa como `from router import classify`
classify = classify_prompt
