# ===== generator_relations.py =====

import json
import re
import requests

from config import MODELS, OPTIONS, OLLAMA_URL


VALID_RELATIONS = {"ONE_TO_MANY", "MANY_TO_MANY"}


DOMAIN_RELATIONS = {
    ("Cliente", "Encomenda"): "ONE_TO_MANY",
    ("Encomenda", "Produto"): "MANY_TO_MANY",
    ("Encomenda", "Pagamento"): "ONE_TO_MANY",
    ("Produto", "Categoria"): "ONE_TO_MANY",
    ("Fornecedor", "Produto"): "MANY_TO_MANY",
    ("Paciente", "Consulta"): "ONE_TO_MANY",
    ("Medico", "Consulta"): "ONE_TO_MANY",
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

        if t not in VALID_RELATIONS:
            t = "ONE_TO_MANY"

        key = (a, b)

        if key in seen:
            continue

        seen.add(key)

        fixed.append({
            "from": a,
            "to": b,
            "type": t,
            "label": rel.get("label", "")
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


# =========================
# CORE LOGIC
# =========================

def _infer_relations(plan: dict, entity_names: list = None):
    if entity_names is None:
        entity_names = _extract_entity_names(plan)

    entities = {name for name in entity_names if name}
    relations = []
    seen = set()

    # 1. DOMAIN RULES
    for (a, b), t in DOMAIN_RELATIONS.items():
        if a in entities and b in entities:
            relations.append({
                "from": a,
                "to": b,
                "type": t,
                "label": ""
            })
            seen.add((a, b))

    # 2. SUPPORT ENTITIES CONNECTION
    entity_list = list(entities)

    main_entities = [
        e for e in entity_list
        if not any(k in e.lower() for k in ["log", "historico", "config"])
    ]

    main_entity = main_entities[0] if main_entities else (entity_list[0] if entity_list else None)

    for e in entity_list:
        if any(k in e.lower() for k in ["log", "historico", "config"]):
            if main_entity and main_entity != e and (main_entity, e) not in seen:
                relations.append({
                    "from": main_entity,
                    "to": e,
                    "type": "ONE_TO_MANY",
                    "label": ""
                })
                seen.add((main_entity, e))

    # 3. FALLBACK CONTROLADO
    if not relations and len(entity_list) >= 2:
        for i in range(len(entity_list) - 1):
            a = entity_list[i]
            b = entity_list[i + 1]

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

Generate only logical business relationships between them.
Do not invent relationships that do not make business sense.
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

    final = []
    seen = set()

    for rel in llm_relations + inferred:
        if not isinstance(rel, dict):
            continue

        a = rel.get("from", "")
        b = rel.get("to", "")

        if not a or not b or a == b:
            continue

        if a not in allowed_entities or b not in allowed_entities:
            continue

        rel_type = str(rel.get("type", "ONE_TO_MANY")).upper().strip()

        if rel_type not in VALID_RELATIONS:
            rel_type = "ONE_TO_MANY"

        key = (a, b)

        if key in seen:
            continue

        seen.add(key)

        final.append({
            "from": a,
            "to": b,
            "type": rel_type,
            "label": rel.get("label", "")
        })

    print(f"[generator_relations] {len(final)} relações geradas")

    return {"relations": final}
