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
    except:
        return None


def _normalize_output(data: dict) -> list:
    relations = data.get("relations", [])
    fixed = []
    seen = set()

    for rel in relations:
        a = rel.get("from", "").strip()
        b = rel.get("to", "").strip()
        t = rel.get("type", "ONE_TO_MANY").upper()

        if not a or not b or a == b:
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


# =========================
# 🔥 CORE LOGIC
# =========================

def _infer_relations(plan: dict):
    entities = {e["name"] for e in plan.get("entities", [])}
    relations = []
    seen = set()

    # 1️⃣ DOMAIN RULES
    for (a, b), t in DOMAIN_RELATIONS.items():
        if a in entities and b in entities:
            relations.append({"from": a, "to": b, "type": t})
            seen.add((a, b))

    # 2️⃣ CONNECT ALL ENTITIES (avoid isolation)
    entity_list = list(entities)

    for i in range(len(entity_list) - 1):
        a = entity_list[i]
        b = entity_list[i + 1]

        if (a, b) not in seen:
            relations.append({
                "from": a,
                "to": b,
                "type": "ONE_TO_MANY"
            })
            seen.add((a, b))

    # 3️⃣ SUPPORT ENTITIES CONNECTION
    for e in entity_list:
        if any(k in e.lower() for k in ["log", "historico", "config"]):
            for target in entity_list:
                if target != e:
                    relations.append({
                        "from": target,
                        "to": e,
                        "type": "ONE_TO_MANY"
                    })
                    break

    return relations


# =========================
# MAIN
# =========================

def generate_relations(plan: dict):

    # 🔥 Extrair APENAS os nomes das entidades para reduzir o uso de tokens/VRAM
    entity_names = [e.get("name") for e in plan.get("entities", []) if "name" in e]
    
    if not entity_names:
        print("[generator_relations] Nenhuma entidade encontrada para relacionar.")
        return {"relations": _infer_relations(plan)}

    # 🔥 Prompt ultra focado
    prompt = f"""
    Given the following entities: {', '.join(entity_names)}.
    Generate logical business relationships between them.
    Respond ONLY with a JSON object strictly matching this format:
    {{"relations": [{{"from": "EntityA", "to": "EntityB", "type": "ONE_TO_MANY"}}]}}
    Allowed types are: ONE_TO_MANY, MANY_TO_MANY.
    """

    payload = {
        "model": MODELS["generator_relations"],
        "prompt": prompt,
        "format": "json", # 🔥 Força o Ollama a devolver apenas JSON válido
        "stream": False,
        "options": OPTIONS
    }

    llm_relations = []

    try:
        # Reduzimos o timeout porque com o "format: json" e menos contexto, ele responde mais rápido
        res = requests.post(OLLAMA_URL, json=payload, timeout=40)
        res.raise_for_status()

        raw = res.json().get("response", "")
        
        # Tentamos ler diretamente como JSON, se falhar usamos o _extract_json por segurança
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = _extract_json(raw)
        
        if data and "relations" in data:
            llm_relations = _normalize_output(data)

    except Exception as e:
        print(f"[generator_relations] erro LLM: {e}")

    inferred = _infer_relations(plan)

    # 🔥 MERGE
    final = []
    seen = set()

    for rel in llm_relations + inferred:
        key = (rel["from"], rel["to"])
        if key in seen:
            continue
        seen.add(key)
        final.append(rel)

    print(f"[generator_relations] {len(final)} relações geradas")

    return {"relations": final}
