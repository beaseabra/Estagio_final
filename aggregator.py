# ===== aggregator.py =====
from typing import Dict, Any
from models import parse_blueprint

def build_final_blueprint(objects: list, relations: list, workspaces: list, actions: list) -> Dict[str, Any]:
    """
    Recebe as listas parciais dos vários geradores, junta tudo, e passa 
    pela armadura Pydantic para garantir que não há nulls ou chaves inválidas.
    """
    # Montar um dict bruto com o que veio dos geradores, protegendo contra não-listas
    raw_blueprint = {
        "objects": objects if isinstance(objects, list) else [],
        "relations": relations if isinstance(relations, list) else [],
        "workspaces": workspaces if isinstance(workspaces, list) else [],
        "actions": actions if isinstance(actions, list) else []
    }
    
    # 🛡️ Passar pela armadura Pydantic. 
    # Isto limpa automaticamente nulls, formata nomes, adiciona chaves ausentes 
    # e devolve um dict 100% seguro.
    clean_blueprint = parse_blueprint(raw_blueprint).to_dict()
    
    # Validação visual no terminal
    total_objs = len(clean_blueprint.get('objects', []))
    print(f"[aggregator] OK — {total_objs} objetos montados de forma segura.")
    
    return clean_blueprint
