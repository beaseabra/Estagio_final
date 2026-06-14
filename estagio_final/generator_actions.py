# ===== generator_actions.py =====

import json
import re
import requests

from config import MODELS, OPTIONS, OLLAMA_URL


# =========================
# 🔥 PROMPT MELHORADO
# =========================

SYSTEM_PROMPT = """
You are a senior business systems architect.

Generate REALISTIC, DETAILED, PRODUCTION-GRADE business actions.

CRITICAL RULE: All generated text, action names, descriptions, steps, preconditions, and postconditions MUST be written strictly in European Portuguese (pt-PT). Keep structural JSON keys in English.

OUTPUT ONLY VALID JSON.

REQUIREMENTS:

- Each action must include:
  • meaningful steps (multi-step logic)
  • preconditions
  • postconditions
  • trigger (manual, automated, scheduled)

- Include:
  • domain workflows (ProcessarEncomenda, AgendarConsulta)
  • automation actions (ReporStockAutomatico)
  • validation actions
  • reporting actions

- Actions must reflect real business processes.

- Minimum:
  • 15–25 actions for complex systems

Return ONLY JSON.
"""


CRUD_TEMPLATE = ["Criar", "Atualizar", "Arquivar", "Listar", "Detalhe"]


# =========================
# UTILS
# =========================

def _extract_json(text: str):
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group())
    except:
        return None


def _normalize_output(data: dict) -> dict:
    actions = data.get("actions", [])
    fixed = []
    seen = set()

    for action in actions:
        name = action.get("name", "").strip()
        if not name or name in seen:
            continue

        seen.add(name)

        fixed.append({
            "name": name,
            "type": action.get("type", "AÇÃO DE DOMÍNIO"),
            "description": action.get("description", ""),
            "trigger": action.get("trigger", "manual"),
            "entities_involved": action.get("entities_involved", []),
            "steps": action.get("steps", []),
            "preconditions": action.get("preconditions", []),
            "postconditions": action.get("postconditions", [])
        })

    return {"actions": fixed}


# =========================
# 🔥 ENRICH ACTION (CORE)
# =========================

def _enrich_action(action: dict) -> dict:
    name = action["name"].lower()

    # 🔥 SMART ENRICHMENT
    if "encomenda" in name:

        action["steps"] = [
            "validar cliente",
            "verificar stock",
            "reservar produtos",
            "criar registo de encomenda",
            "calcular total",
            "atualizar estado",
            "emitir notificação"
        ]

        action["preconditions"] = [
            "cliente deve existir",
            "produtos devem estar disponíveis"
        ]

        action["postconditions"] = [
            "encomenda criada",
            "stock atualizado"
        ]

    elif "stock" in name:

        action["steps"] = [
            "verificar níveis de stock",
            "comparar com stock mínimo",
            "gerar ordem de reposição",
            "notificar responsável"
        ]

        action["trigger"] = "automatizado"

    elif "pagamento" in name:

        action["steps"] = [
            "validar método de pagamento",
            "processar transação",
            "confirmar pagamento",
            "atualizar estado financeiro"
        ]

    elif "relatorio" in name:

        action["trigger"] = "agendado"

        action["steps"] = [
            "recolher dados",
            "agregar métricas",
            "gerar relatório",
            "armazenar resultados"
        ]

    return action


# =========================
# CRUD ACTIONS
# =========================

def _generate_crud_actions(plan: dict) -> list:
    actions = []

    for entity in plan.get("entities", []):
        name = entity.get("name", "")

        for verb in CRUD_TEMPLATE:
            actions.append({
                "name": f"{verb}{name}",
                "type": "OPERAÇÃO BÁSICA",
                "description": f"{verb} {name} no sistema",
                "trigger": "manual",
                "entities_involved": [name],
                "steps": [f"{verb.lower()} registo de {name}"],
                "preconditions": [],
                "postconditions": []
            })

    return actions


# =========================
# MAIN
# =========================

def generate_actions(plan: dict) -> dict:

    payload = {
        "model": MODELS.get("generator_actions", MODELS["generator_objects"]),
        "prompt": f"{SYSTEM_PROMPT}\n\nPlan:\n{json.dumps(plan, indent=2, ensure_ascii=False)}",
        "stream": False,
        "options": {**OPTIONS, "num_predict": 2048}
    }

    llm_actions = []

    try:
        response = requests.post(OLLAMA_URL, json=payload, timeout=90)
        response.raise_for_status()

        raw = response.json().get("response", "")
        data = _extract_json(raw)

        if data:
            llm_actions = _normalize_output(data)["actions"]

    except Exception as e:
        print(f"[generator_actions] erro LLM: {e}")

    # =========================
    # MERGE + ENRICH
    # =========================

    all_actions = []
    seen = set()

    for action in llm_actions + _generate_crud_actions(plan):

        if action["name"] in seen:
            continue

        seen.add(action["name"])

        action = _enrich_action(action)
        all_actions.append(action)

    print(f"[generator_actions] {len(all_actions)} ações geradas")

    return {"actions": all_actions}
