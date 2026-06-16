# ===== generator_relations.py =====

import json
import re
import requests

from config import MODELS, OPTIONS, OLLAMA_URL


VALID_RELATIONS = {"ONE_TO_MANY", "MANY_TO_MANY"}


# Relações canónicas conhecidas.
# Nota: Categoria -> Produto é mais correto do que Produto -> Categoria,
# porque uma categoria pode conter muitos produtos.
DOMAIN_RELATIONS = {
    ("Cliente", "Encomenda"): "ONE_TO_MANY",
    ("Encomenda", "Produto"): "MANY_TO_MANY",
    ("Encomenda", "Pagamento"): "ONE_TO_MANY",
    ("Categoria", "Produto"): "ONE_TO_MANY",
    ("Fornecedor", "Produto"): "MANY_TO_MANY",

    ("Paciente", "Consulta"): "ONE_TO_MANY",
    ("Medico", "Consulta"): "ONE_TO_MANY",
    ("Médico", "Consulta"): "ONE_TO_MANY",

    ("Aluno", "Inscricao"): "ONE_TO_MANY",
    ("Aluno", "Inscrição"): "ONE_TO_MANY",
    ("Curso", "Inscricao"): "ONE_TO_MANY",
    ("Curso", "Inscrição"): "ONE_TO_MANY",

    ("Conta", "Transacao"): "ONE_TO_MANY",
    ("Conta", "Transação"): "ONE_TO_MANY",
    ("Cliente", "Conta"): "ONE_TO_MANY",
}


SUPPORT_ENTITY_KEYWORDS = {
    "log",
    "logsistema",
    "historico",
    "histórico",
    "config",
    "configuracao",
    "configuração"
}


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


def _normalize_entity_name(name: str) -> str:
    return str(name).strip()


def _lower_token(name: str) -> str:
    return re.sub(r"[^a-z0-9áàâãéèêíìóòôõúùç]", "", str(name).lower())


def _is_support_entity(name: str) -> bool:
    token = _lower_token(name)
    return any(k in token for k in SUPPORT_ENTITY_KEYWORDS)


def _is_known_domain_context(entities: set) -> bool:
    """
    Ativa filtragem mais rigorosa quando o domínio é reconhecível.
    Isto evita aceitar relações fracas do LLM em domínios comuns.
    """
    ecommerce = {"Cliente", "Produto", "Encomenda", "Pagamento"}
    hospital = {"Paciente", "Consulta"}
    education = {"Aluno", "Curso"}
    finance = {"Cliente", "Conta"}

    return (
        len(ecommerce.intersection(entities)) >= 3
        or len(hospital.intersection(entities)) >= 2
        or len(education.intersection(entities)) >= 2
        or len(finance.intersection(entities)) >= 2
    )


def _support_relation_allowed(a: str, b: str, entities: set) -> bool:
    """
    Permite apenas ligações de suporte conservadoras.
    Não queremos relações aleatórias como LogSistema -> Produto.
    """
    if b in {"Historico", "Histórico"} and not _is_support_entity(a):
        return True

    if b == "LogSistema" and not _is_support_entity(a):
        return True

    if a in {"Configuracao", "Configuração"} and b == "LogSistema":
        return True

    return False


def _business_relation_allowed(a: str, b: str, entities: set) -> bool:
    """
    Decide se uma relação faz sentido.
    Em domínios conhecidos, aceita apenas relações canónicas ou suporte.
    Em domínios desconhecidos, é mais permissivo, mas bloqueia relações de suporte erradas.
    """
    if (a, b) in DOMAIN_RELATIONS:
        return True

    if _support_relation_allowed(a, b, entities):
        return True

    known_context = _is_known_domain_context(entities)

    if known_context:
        return False

    # Para domínios desconhecidos, bloquear relações em que uma entidade de suporte
    # aparece como origem de uma entidade de negócio.
    if _is_support_entity(a) and not _is_support_entity(b):
        return False

    return True


def _normalize_output(data: dict, allowed_entities: set = None) -> list:
    relations = data.get("relations", [])

    if not isinstance(relations, list):
        relations = []

    fixed = []
    seen = set()

    for rel in relations:
        if not isinstance(rel, dict):
            continue

        a = _normalize_entity_name(rel.get("from", ""))
        b = _normalize_entity_name(rel.get("to", ""))
        t = str(rel.get("type", "ONE_TO_MANY")).upper().strip()

        if not a or not b or a == b:
            continue

        if allowed_entities:
            if a not in allowed_entities or b not in allowed_entities:
                continue

            if not _business_relation_allowed(a, b, allowed_entities):
                continue

        if t not in VALID_RELATIONS:
            t = "ONE_TO_MANY"

        key = (a, b)

        if key in seen:
            continue

        # Evita duplicados invertidos dentro do próprio output do LLM.
        if (b, a) in seen:
            continue

        seen.add(key)

        fixed.append({
            "from": a,
            "to": b,
            "type": t,
            "label": str(rel.get("label", "")).strip()
        })

    return fixed


def _extract_entity_names(plan: dict, objects_data: dict = None) -> list:
    """
    Preferir os objetos finais já gerados.
    Se não existirem, usar as entidades do plano.
    """
    if (
        objects_data
        and isinstance(objects_data, dict)
        and isinstance(objects_data.get("objects"), list)
        and objects_data.get("objects")
    ):
        names = [
            obj.get("name")
            for obj in objects_data.get("objects", [])
            if isinstance(obj, dict) and obj.get("name")
        ]
    else:
        names = [
            e.get("name")
            for e in plan.get("entities", [])
            if isinstance(e, dict) and e.get("name")
        ]

    clean = []
    seen = set()

    for name in names:
        name = _normalize_entity_name(name)

        if not name or name in seen:
            continue

        seen.add(name)
        clean.append(name)

    return clean


def _choose_main_entity(entity_names: list) -> str:
    """
    Escolhe uma entidade principal para ligar entidades de suporte.
    Em loja online, Encomenda costuma ser a melhor entidade central.
    """
    priority = [
        "Encomenda",
        "Pedido",
        "Consulta",
        "Processo",
        "Projeto",
        "Conta",
        "Cliente",
        "Produto"
    ]

    for p in priority:
        if p in entity_names:
            return p

    for e in entity_names:
        if not _is_support_entity(e):
            return e

    return entity_names[0] if entity_names else ""


# =========================
# CORE LOGIC
# =========================

def _infer_relations(plan: dict, entity_names: list = None):
    if entity_names is None:
        entity_names = _extract_entity_names(plan)

    entities = {name for name in entity_names if name}
    relations = []
    seen = set()

    # 1. Domain rules canónicas
    for (a, b), t in DOMAIN_RELATIONS.items():
        if a in entities and b in entities:
            relations.append({
                "from": a,
                "to": b,
                "type": t,
                "label": ""
            })
            seen.add((a, b))

    # 2. Ligações conservadoras para entidades de suporte
    main_entity = _choose_main_entity(entity_names)

    if main_entity:
        if "Historico" in entities and (main_entity, "Historico") not in seen:
            relations.append({
                "from": main_entity,
                "to": "Historico",
                "type": "ONE_TO_MANY",
                "label": ""
            })
            seen.add((main_entity, "Historico"))

        if "Histórico" in entities and (main_entity, "Histórico") not in seen:
            relations.append({
                "from": main_entity,
                "to": "Histórico",
                "type": "ONE_TO_MANY",
                "label": ""
            })
            seen.add((main_entity, "Histórico"))

        if "LogSistema" in entities and (main_entity, "LogSistema") not in seen:
            relations.append({
                "from": main_entity,
                "to": "LogSistema",
                "type": "ONE_TO_MANY",
                "label": ""
            })
            seen.add((main_entity, "LogSistema"))

    if (
        "Configuracao" in entities
        and "LogSistema" in entities
        and ("Configuracao", "LogSistema") not in seen
    ):
        relations.append({
            "from": "Configuracao",
            "to": "LogSistema",
            "type": "ONE_TO_MANY",
            "label": ""
        })
        seen.add(("Configuracao", "LogSistema"))

    if (
        "Configuração" in entities
        and "LogSistema" in entities
        and ("Configuração", "LogSistema") not in seen
    ):
        relations.append({
            "from": "Configuração",
            "to": "LogSistema",
            "type": "ONE_TO_MANY",
            "label": ""
        })
        seen.add(("Configuração", "LogSistema"))

    # 3. Fallback mínimo só se não houver nenhuma relação
    if not relations and len(entity_names) >= 2:
        business_entities = [
            e for e in entity_names
            if not _is_support_entity(e)
        ]

        if len(business_entities) >= 2:
            for i in range(len(business_entities) - 1):
                a = business_entities[i]
                b = business_entities[i + 1]

                if (a, b) not in seen:
                    relations.append({
                        "from": a,
                        "to": b,
                        "type": "ONE_TO_MANY",
                        "label": ""
                    })
                    seen.add((a, b))

    return relations


# =========================
# MAIN
# =========================

def generate_relations(plan: dict, objects_data: dict = None):
    entity_names = _extract_entity_names(plan, objects_data)

    if not entity_names:
        print("[generator_relations] Nenhuma entidade encontrada para relacionar.")
        return {"relations": _infer_relations(plan, entity_names)}

    allowed_entities = set(entity_names)

    prompt = f"""
Given the following entities: {', '.join(entity_names)}.

Generate only strong and logical business relationships between them.
Do not generate reverse duplicates.
Do not connect support entities such as LogSistema, Historico or Configuracao to normal business entities unless it is clearly an audit/history relation.
Prefer fewer, correct relationships over many weak relationships.

Respond ONLY with a JSON object strictly matching this format:
{{"relations": [{{"from": "EntityA", "to": "EntityB", "type": "ONE_TO_MANY", "label": ""}}]}}

Allowed types are: ONE_TO_MANY, MANY_TO_MANY.
Allowed entity names are exactly: {', '.join(entity_names)}.
"""

    payload = {
        "model": MODELS["generator_relations"],
        "prompt": prompt,
        "format": "json",
        "stream": False,
        "options": OPTIONS
    }

    llm_relations = []

    try:
        res = requests.post(
            OLLAMA_URL,
            json=payload,
            timeout=60
        )

        res.raise_for_status()

        raw = res.json().get("response", "")

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = _extract_json(raw)

        if data and "relations" in data:
            llm_relations = _normalize_output(data, allowed_entities)

    except Exception as e:
        print(f"[generator_relations] erro LLM: {e}")

    inferred = _infer_relations(plan, entity_names)

    # Merge final.
    # Importante: inferred vem primeiro, para preservar as relações canónicas.
    final = []
    seen = set()

    for rel in inferred + llm_relations:
        if not isinstance(rel, dict):
            continue

        a = _normalize_entity_name(rel.get("from", ""))
        b = _normalize_entity_name(rel.get("to", ""))

        if not a or not b or a == b:
            continue

        if a not in allowed_entities or b not in allowed_entities:
            continue

        if not _business_relation_allowed(a, b, allowed_entities):
            continue

        rel_type = str(rel.get("type", "ONE_TO_MANY")).upper().strip()

        if rel_type not in VALID_RELATIONS:
            rel_type = "ONE_TO_MANY"

        key = (a, b)
        reverse_key = (b, a)

        if key in seen:
            continue

        # Evita relações duplicadas em sentidos opostos.
        if reverse_key in seen:
            continue

        seen.add(key)

        final.append({
            "from": a,
            "to": b,
            "type": rel_type,
            "label": str(rel.get("label", "")).strip()
        })

    print(f"[generator_relations] {len(final)} relações geradas")

    return {"relations": final}
