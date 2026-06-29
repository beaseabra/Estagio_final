# ===== generator_actions.py =====

CRUD_TEMPLATE = ["Criar", "Atualizar", "Arquivar", "Listar", "Detalhe"]

SUPPORT_KEYWORDS = [
    "log",
    "historico",
    "histórico",
    "config",
    "configuracao",
    "configuração"
]


# =========================
# UTILS
# =========================

def _as_list(value):
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]

    if isinstance(value, str) and value.strip():
        return [value.strip()]

    return []


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


# =========================
# CRUD DETAILS
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
                "verificar se o registo pode ser arquivado",
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
                "apresentar lista ao utilizador"
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

    return {
        "type": "CRUD_ACTION",
        "trigger": "manual",
        "steps": [
            f"executar operação sobre {entity_low}"
        ],
        "preconditions": [],
        "postconditions": []
    }


# =========================
# CRUD ACTIONS
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
                "postconditions": details["postconditions"]
            })

    return actions


# =========================
# DOMAIN ACTIONS
# =========================

def _generate_domain_actions(plan: dict) -> list:
    entities = set(_extract_entities(plan))
    actions = []

    # Loja online / e-commerce
    if "Encomenda" in entities:
        actions.append({
            "name": "ProcessarEncomenda",
            "type": "DOMAIN_ACTION",
            "description": "Processar uma encomenda desde a validação até à notificação ao cliente",
            "trigger": "manual",
            "entities_involved": [
                e for e in ["Encomenda", "Cliente", "Produto"]
                if e in entities
            ],
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
        })

        actions.append({
            "name": "ValidarEncomenda",
            "type": "VALIDATION_ACTION",
            "description": "Validar a consistência de uma encomenda antes do processamento",
            "trigger": "manual",
            "entities_involved": ["Encomenda"],
            "steps": [
                "verificar dados do cliente",
                "validar produtos associados",
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
        })

    if "Pagamento" in entities:
        actions.append({
            "name": "ConfirmarPagamento",
            "type": "DOMAIN_ACTION",
            "description": "Confirmar pagamento e atualizar o estado financeiro da encomenda",
            "trigger": "manual",
            "entities_involved": [
                e for e in ["Pagamento", "Encomenda"]
                if e in entities
            ],
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
        })

    if "Produto" in entities:
        actions.append({
            "name": "ReporStockAutomatico",
            "type": "AUTOMATED_ACTION",
            "description": "Repor stock automaticamente quando produtos atingem níveis mínimos",
            "trigger": "automated",
            "entities_involved": ["Produto"],
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
        })

    if "Encomenda" in entities and "Pagamento" in entities:
        actions.append({
            "name": "GerarRelatorioVendas",
            "type": "REPORT_ACTION",
            "description": "Gerar relatório periódico de vendas e pagamentos",
            "trigger": "scheduled",
            "entities_involved": [
                e for e in ["Encomenda", "Pagamento", "Produto"]
                if e in entities
            ],
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
        })

    # Hospital
    if "Consulta" in entities:
        actions.append({
            "name": "AgendarConsulta",
            "type": "DOMAIN_ACTION",
            "description": "Agendar consulta para um paciente com um profissional disponível",
            "trigger": "manual",
            "entities_involved": [
                e for e in ["Consulta", "Paciente", "Medico", "Médico"]
                if e in entities
            ],
            "steps": [
                "validar dados do paciente",
                "consultar disponibilidade do profissional",
                "criar marcação da consulta",
                "notificar paciente"
            ],
            "preconditions": [
                "paciente deve existir",
                "profissional deve ter disponibilidade"
            ],
            "postconditions": [
                "consulta agendada",
                "paciente notificado"
            ]
        })

    return actions


# =========================
# MAIN
# =========================

def generate_actions(plan: dict) -> dict:
    all_actions = []
    seen = set()

    generated_actions = (
        _generate_domain_actions(plan)
        + _generate_crud_actions(plan)
    )

    for action in generated_actions:
        name = str(action.get("name", "")).strip()

        if not name or name in seen:
            continue

        seen.add(name)

        action["entities_involved"] = _as_list(action.get("entities_involved", []))
        action["steps"] = _as_list(action.get("steps", []))
        action["preconditions"] = _as_list(action.get("preconditions", []))
        action["postconditions"] = _as_list(action.get("postconditions", []))

        all_actions.append(action)

    print(f"[generator_actions] {len(all_actions)} ações geradas")

    return {"actions": all_actions}
