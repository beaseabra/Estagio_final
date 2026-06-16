# ===== generator_actions.py =====

import json
import re
import requests

from config import MODELS, OPTIONS, OLLAMA_URL


# =========================
# PROMPT
# =========================

SYSTEM_PROMPT = """
You are a senior business systems architect.

Generate REALISTIC, DETAILED, PRODUCTION-GRADE business actions.

CRITICAL RULE:
All generated text, action names, descriptions, steps, preconditions, and postconditions MUST be written strictly in European Portuguese (pt-PT).
Keep structural JSON keys in English.

OUTPUT ONLY VALID JSON strictly matching this format:
{
  "actions": [
    {
      "name": "NomeDaAcao",
      "type": "DOMAIN_ACTION",
      "description": "Descrição da ação",
      "trigger": "manual",
      "entities_involved": ["Entidade"],
      "steps": ["passo 1", "passo 2"],
      "preconditions": ["pré-condição"],
      "postconditions": ["pós-condição"]
    }
  ]
}

Allowed action types:
DOMAIN_ACTION, CRUD_ACTION, REPORT_ACTION, NOTIFICATION_ACTION, VALIDATION_ACTION, INTEGRATION_ACTION, AUTOMATED_ACTION.

Allowed triggers:
manual, automated, scheduled.

Generate actions that reflect real business processes.
Prefer useful actions over many repetitive actions.
Return ONLY JSON.
"""


CRUD_TEMPLATE = ["Criar", "Atualizar", "Arquivar", "Listar", "Detalhe"]

VALID_ACTION_TYPES = {
    "DOMAIN_ACTION",
    "CRUD_ACTION",
    "REPORT_ACTION",
    "NOTIFICATION_ACTION",
    "VALIDATION_ACTION",
    "INTEGRATION_ACTION",
    "AUTOMATED_ACTION",
}

VALID_TRIGGERS = {"manual", "automated", "scheduled"}

SUPPORT_KEYWORDS = ["log", "historico", "histórico", "config", "configuracao", "configuração"]


# =========================
# UTILS
# =========================

def _extract_json(text: str):
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None

    try:
        return json.loads(match.group())
    except Exception:
        return None


def _as_list(value):
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]

    if isinstance(value, str) and value.strip():
        return [value.strip()]

    return []


def _normalize_action_type(value: str) -> str:
    raw = str(value or "").strip().upper()

    aliases = {
        "AÇÃO DE DOMÍNIO": "DOMAIN_ACTION",
        "ACAO DE DOMINIO": "DOMAIN_ACTION",
        "OPERAÇÃO BÁSICA": "CRUD_ACTION",
        "OPERACAO BASICA": "CRUD_ACTION",
        "CRUD": "CRUD_ACTION",
        "RELATÓRIO": "REPORT_ACTION",
        "RELATORIO": "REPORT_ACTION",
        "AUTOMAÇÃO": "AUTOMATED_ACTION",
        "AUTOMACAO": "AUTOMATED_ACTION",
        "VALIDAÇÃO": "VALIDATION_ACTION",
        "VALIDACAO": "VALIDATION_ACTION",
    }

    if raw in VALID_ACTION_TYPES:
        return raw

    return aliases.get(raw, "DOMAIN_ACTION")


def _normalize_trigger(value: str) -> str:
    raw = str(value or "").strip().lower()

    aliases = {
        "manual": "manual",
        "automatico": "automated",
        "automático": "automated",
        "automatizado": "automated",
        "automated": "automated",
        "scheduled": "scheduled",
        "agendado": "scheduled",
        "recorrente": "scheduled",
    }

    return aliases.get(raw, "manual")


def _is_support_entity(name: str) -> bool:
    low = str(name).lower()
    return any(k in low for k in SUPPORT_KEYWORDS)


def _extract_entities(plan: dict) -> list:
    entities = plan.get("entities", [])

    if not isinstance(entities, list):
        return []

    result = []
    seen = set()

    for entity in entities:
        if not isinstance(entity, dict):
            continue

        name = str(entity.get("name", "")).strip()

        if not name or name in seen:
            continue

        if _is_support_entity(name):
            continue

        seen.add(name)
        result.append(name)

    return result


def _operation_from_name(action_name: str) -> str:
    for verb in CRUD_TEMPLATE:
        if action_name.lower().startswith(verb.lower()):
            return verb

    if action_name.lower().startswith("processar"):
        return "Processar"

    if action_name.lower().startswith("validar"):
        return "Validar"

    if action_name.lower().startswith("confirmar"):
        return "Confirmar"

    if action_name.lower().startswith("gerar"):
        return "Gerar"

    if action_name.lower().startswith("repor"):
        return "Repor"

    return ""


def _entity_from_action_name(action_name: str, known_entities: list) -> str:
    low = action_name.lower()

    for entity in sorted(known_entities, key=len, reverse=True):
        if entity.lower() in low:
            return entity

    return known_entities[0] if known_entities else ""


# =========================
# NORMALIZATION
# =========================

def _normalize_output(data: dict, known_entities: list) -> dict:
    actions = data.get("actions", [])

    if not isinstance(actions, list):
        actions = []

    fixed = []
    seen = set()

    for action in actions:
        if not isinstance(action, dict):
            continue

        name = str(action.get("name", "")).strip()

        if not name or name in seen:
            continue

        seen.add(name)

        entities_involved = _as_list(action.get("entities_involved", []))
        entities_involved = [e for e in entities_involved if e in known_entities]

        if not entities_involved:
            inferred_entity = _entity_from_action_name(name, known_entities)
            entities_involved = [inferred_entity] if inferred_entity else []

        fixed.append({
            "name": name,
            "type": _normalize_action_type(action.get("type", "DOMAIN_ACTION")),
            "description": str(action.get("description", "")).strip(),
            "trigger": _normalize_trigger(action.get("trigger", "manual")),
            "entities_involved": entities_involved,
            "steps": _as_list(action.get("steps", [])),
            "preconditions": _as_list(action.get("preconditions", [])),
            "postconditions": _as_list(action.get("postconditions", [])),
        })

    return {"actions": fixed}


# =========================
# ACTION DETAILS
# =========================

def _crud_details(verb: str, entity: str) -> dict:
    entity_low = entity.lower()

    if verb == "Criar":
        return {
            "type": "CRUD_ACTION",
            "trigger": "manual",
            "steps": [
                f"validar dados obrigatórios de {entity_low}",
                f"verificar duplicados de {entity_low}",
                f"criar registo de {entity_low}",
                "confirmar operação"
            ],
            "preconditions": [
                "utilizador deve ter permissão de criação"
            ],
            "postconditions": [
                f"{entity_low} criado com sucesso"
            ]
        }

    if verb == "Atualizar":
        return {
            "type": "CRUD_ACTION",
            "trigger": "manual",
            "steps": [
                f"localizar registo de {entity_low}",
                "validar alterações submetidas",
                f"atualizar dados de {entity_low}",
                "registar alteração no histórico"
            ],
            "preconditions": [
                f"{entity_low} deve existir",
                "utilizador deve ter permissão de edição"
            ],
            "postconditions": [
                f"{entity_low} atualizado com sucesso"
            ]
        }

    if verb == "Arquivar":
        return {
            "type": "CRUD_ACTION",
            "trigger": "manual",
            "steps": [
                f"localizar registo de {entity_low}",
                "verificar se pode ser arquivado",
                "marcar registo como inativo",
                "registar motivo de arquivo"
            ],
            "preconditions": [
                f"{entity_low} deve existir",
                "registo não deve estar bloqueado"
            ],
            "postconditions": [
                f"{entity_low} arquivado com sucesso"
            ]
        }

    if verb == "Listar":
        return {
            "type": "CRUD_ACTION",
            "trigger": "manual",
            "steps": [
                "receber filtros de pesquisa",
                f"consultar lista de {entity_low}",
                "ordenar e paginar resultados",
                "devolver lista ao utilizador"
            ],
            "preconditions": [
                "utilizador deve ter permissão de leitura"
            ],
            "postconditions": [
                f"lista de {entity_low} apresentada"
            ]
        }

    if verb == "Detalhe":
        return {
            "type": "CRUD_ACTION",
            "trigger": "manual",
            "steps": [
                f"localizar registo de {entity_low}",
                "carregar informação relacionada",
                "formatar detalhe do registo",
                "apresentar informação ao utilizador"
            ],
            "preconditions": [
                f"{entity_low} deve existir",
                "utilizador deve ter permissão de leitura"
            ],
            "postconditions": [
                f"detalhe de {entity_low} apresentado"
            ]
        }

    return {}


def _domain_details(action_name: str) -> dict:
    low = action_name.lower()

    if "processarencomenda" in low or "processar_encomenda" in low:
        return {
            "type": "DOMAIN_ACTION",
            "trigger": "manual",
            "steps": [
                "validar cliente associado à encomenda",
                "validar disponibilidade dos produtos",
                "reservar stock dos produtos",
                "calcular total da encomenda",
                "atualizar estado da encomenda",
                "emitir notificação ao cliente"
            ],
            "preconditions": [
                "cliente deve existir",
                "encomenda deve estar pendente",
                "produtos devem ter stock disponível"
            ],
            "postconditions": [
                "encomenda processada",
                "stock reservado",
                "cliente notificado"
            ]
        }

    if "validarencomenda" in low or "validar_encomenda" in low:
        return {
            "type": "VALIDATION_ACTION",
            "trigger": "manual",
            "steps": [
                "verificar dados do cliente",
                "validar linhas da encomenda",
                "confirmar disponibilidade de stock",
                "validar valor total",
                "marcar encomenda como validada"
            ],
            "preconditions": [
                "encomenda deve existir"
            ],
            "postconditions": [
                "encomenda validada"
            ]
        }

    if "confirmarpagamento" in low or "confirmar_pagamento" in low:
        return {
            "type": "DOMAIN_ACTION",
            "trigger": "manual",
            "steps": [
                "validar referência da transação",
                "confirmar valor recebido",
                "associar pagamento à encomenda",
                "atualizar estado financeiro",
                "emitir comprovativo de pagamento"
            ],
            "preconditions": [
                "pagamento deve existir",
                "encomenda associada deve existir"
            ],
            "postconditions": [
                "pagamento confirmado",
                "estado financeiro atualizado"
            ]
        }

    if "reporstock" in low or "repor_stock" in low:
        return {
            "type": "AUTOMATED_ACTION",
            "trigger": "automated",
            "steps": [
                "identificar produtos abaixo do stock mínimo",
                "calcular quantidade necessária para reposição",
                "gerar pedido de reposição",
                "notificar responsável de compras"
            ],
            "preconditions": [
                "produto deve ter stock mínimo definido"
            ],
            "postconditions": [
                "pedido de reposição criado",
                "responsável notificado"
            ]
        }

    if "relatoriovendas" in low or "relatorio_vendas" in low:
        return {
            "type": "REPORT_ACTION",
            "trigger": "scheduled",
            "steps": [
                "recolher encomendas do período",
                "agregar vendas por produto e cliente",
                "calcular métricas financeiras",
                "gerar relatório de vendas",
                "disponibilizar relatório aos utilizadores autorizados"
            ],
            "preconditions": [
                "existem dados de vendas no período"
            ],
            "postconditions": [
                "relatório de vendas gerado"
            ]
        }

    return {}


def _enrich_action(action: dict, known_entities: list) -> dict:
    name = action.get("name", "")
    operation = _operation_from_name(name)
    entity = _entity_from_action_name(name, known_entities)

    if entity and not action.get("entities_involved"):
        action["entities_involved"] = [entity]

    if operation in CRUD_TEMPLATE and entity:
        details = _crud_details(operation, entity)

        action["type"] = details["type"]
        action["trigger"] = details["trigger"]
        action["steps"] = details["steps"]
        action["preconditions"] = details["preconditions"]
        action["postconditions"] = details["postconditions"]

        if not action.get("description"):
            action["description"] = f"{operation} {entity} no sistema"

        return action

    domain = _domain_details(name)

    if domain:
        action["type"] = domain["type"]
        action["trigger"] = domain["trigger"]
        action["steps"] = domain["steps"]
        action["preconditions"] = domain["preconditions"]
        action["postconditions"] = domain["postconditions"]
        return action

    if not action.get("steps"):
        action["steps"] = [
            "validar pedido recebido",
            "executar operação de negócio",
            "registar resultado da operação"
        ]

    if not action.get("preconditions"):
        action["preconditions"] = [
            "utilizador deve ter permissão para executar a ação"
        ]

    if not action.get("postconditions"):
        action["postconditions"] = [
            "operação concluída com sucesso"
        ]

    action["type"] = _normalize_action_type(action.get("type", "DOMAIN_ACTION"))
    action["trigger"] = _normalize_trigger(action.get("trigger", "manual"))

    return action


# =========================
# GENERATED ACTIONS
# =========================

def _generate_crud_actions(plan: dict) -> list:
    actions = []
    entities = _extract_entities(plan)

    for entity in entities:
        for verb in CRUD_TEMPLATE:
            details = _crud_details(verb, entity)

            actions.append({
                "name": f"{verb}{entity}",
                "type": details["type"],
                "description": f"{verb} {entity} no sistema",
                "trigger": details["trigger"],
                "entities_involved": [entity],
                "steps": details["steps"],
                "preconditions": details["preconditions"],
                "postconditions": details["postconditions"],
            })

    return actions


def _generate_domain_actions(plan: dict) -> list:
    entities = set(_extract_entities(plan))
    actions = []

    if "Encomenda" in entities:
        actions.append({
            "name": "ProcessarEncomenda",
            "type": "DOMAIN_ACTION",
            "description": "Processar uma encomenda desde a validação até à notificação ao cliente",
            "trigger": "manual",
            "entities_involved": ["Encomenda", "Cliente", "Produto"],
            "steps": [],
            "preconditions": [],
            "postconditions": []
        })

        actions.append({
            "name": "ValidarEncomenda",
            "type": "VALIDATION_ACTION",
            "description": "Validar a consistência de uma encomenda antes do processamento",
            "trigger": "manual",
            "entities_involved": ["Encomenda"],
            "steps": [],
            "preconditions": [],
            "postconditions": []
        })

    if "Pagamento" in entities:
        actions.append({
            "name": "ConfirmarPagamento",
            "type": "DOMAIN_ACTION",
            "description": "Confirmar pagamento e atualizar o estado financeiro da encomenda",
            "trigger": "manual",
            "entities_involved": ["Pagamento", "Encomenda"],
            "steps": [],
            "preconditions": [],
            "postconditions": []
        })

    if "Produto" in entities:
        actions.append({
            "name": "ReporStockAutomatico",
            "type": "AUTOMATED_ACTION",
            "description": "Repor stock automaticamente quando produtos atingem níveis mínimos",
            "trigger": "automated",
            "entities_involved": ["Produto"],
            "steps": [],
            "preconditions": [],
            "postconditions": []
        })

    if "Encomenda" in entities and "Pagamento" in entities:
        actions.append({
            "name": "GerarRelatorioVendas",
            "type": "REPORT_ACTION",
            "description": "Gerar relatório periódico de vendas e pagamentos",
            "trigger": "scheduled",
            "entities_involved": ["Encomenda", "Pagamento", "Produto"],
            "steps": [],
            "preconditions": [],
            "postconditions": []
        })

    return actions


# =========================
# MAIN
# =========================

def generate_actions(plan: dict) -> dict:
    known_entities = _extract_entities(plan)

    payload = {
        "model": MODELS.get("generator_actions", MODELS["generator_objects"]),
        "prompt": f"{SYSTEM_PROMPT}\n\nPlan:\n{json.dumps(plan, indent=2, ensure_ascii=False)}",
        "format": "json",
        "stream": False,
        "options": {**OPTIONS, "num_predict": 1200}
    }

    llm_actions = []

    try:
        response = requests.post(
            OLLAMA_URL,
            json=payload,
            timeout=120
        )

        response.raise_for_status()

        raw = response.json().get("response", "")

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = _extract_json(raw)

        if data and "actions" in data:
            llm_actions = _normalize_output(data, known_entities)["actions"]

    except Exception as e:
        print(f"[generator_actions] erro LLM: {e}")

    all_actions = []
    seen = set()

    generated_actions = (
        _generate_domain_actions(plan)
        + _generate_crud_actions(plan)
    )

    for action in llm_actions + generated_actions:
        if not isinstance(action, dict):
            continue

        name = str(action.get("name", "")).strip()

        if not name or name in seen:
            continue

        seen.add(name)

        action["name"] = name
        action["type"] = _normalize_action_type(action.get("type", "DOMAIN_ACTION"))
        action["trigger"] = _normalize_trigger(action.get("trigger", "manual"))
        action["entities_involved"] = [
            e for e in _as_list(action.get("entities_involved", []))
            if e in known_entities
        ]
        action["steps"] = _as_list(action.get("steps", []))
        action["preconditions"] = _as_list(action.get("preconditions", []))
        action["postconditions"] = _as_list(action.get("postconditions", []))

        action = _enrich_action(action, known_entities)
        all_actions.append(action)

    print(f"[generator_actions] {len(all_actions)} ações geradas")

    return {"actions": all_actions}
