# ===== router.py =====
import re
from typing import Dict, Any, Tuple
from models import parse_blueprint

# Palavras-chave que indicam claramente um desejo de alterar algo existente
_UPDATE_KEYWORDS = {
    "adiciona", "adicionar", "remove", "remover", "apaga", "apagar", 
    "elimina", "eliminar", "renomeia", "renomear", "altera", "alterar", 
    "muda", "mudar", "atualiza", "atualizar", "cria o", "cria a", 
    "cria um novo", "cria uma nova", "acção", "ação"
}

def _schema_is_active(current_schema: Dict[str, Any]) -> bool:
    """
    Verifica de forma blindada se já existe um projeto ativo na cache.
    """
    if not current_schema:
        return False
    
    # 🛡️ Pydantic garante que não há erros de NoneType ao ler a cache
    bp = parse_blueprint(current_schema)
    
    # Se tem objetos ou workspaces, consideramos que o schema já está ativo
    return len(bp.objects) > 0 or len(bp.workspaces) > 0

def classify_prompt(prompt: str, current_schema: Dict[str, Any]) -> Tuple[str, float]:
    """
    Analisa o prompt e o estado da cache para decidir a rota:
    CREATE_SYSTEM vs UPDATE_SCHEMA
    """
    prompt_lower = prompt.lower()
    has_active_schema = _schema_is_active(current_schema)
    
    print(f"[router] prompt limpo: '{prompt}'")
    print(f"[router] schema ativo: {'Sim' if has_active_schema else 'Não'}")

    # 1. Se o utilizador diz explicitamente "Cria um sistema..." força a base
    if "cria um sistema" in prompt_lower or "cria o sistema" in prompt_lower:
        return "CREATE_SYSTEM", 1.0

    # 2. Se o utilizador quer atualizar algo existente (e o schema está ativo)
    if any(kw in prompt_lower for kw in _UPDATE_KEYWORDS) and has_active_schema:
        return "UPDATE_SCHEMA", 1.0

    # 3. Heurística de fallback:
    # Se já há um schema carregado e o utilizador não pediu explicitamente "um sistema",
    # assumimos que a intenção é adicionar ou modificar (UPDATE_SCHEMA).
    if has_active_schema:
        return "UPDATE_SCHEMA", 0.9

    # 4. Se a cache está vazia, criamos um novo sistema
    return "CREATE_SYSTEM", 0.8
