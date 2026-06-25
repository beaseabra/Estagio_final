# ===== generator_objects.py =====

import json
import re
import requests

from config import MODELS, OPTIONS, OLLAMA_URL


# =========================
#  PROMPT MELHORADO
# =========================

SYSTEM_PROMPT = """
You are a senior system architect designing production-grade business systems.

Your goal is to generate COMPLETE, REALISTIC, HIGH-COMPLEXITY business objects.

CRITICAL RULE: All generated text, entity names, descriptions, and fields MUST be written strictly in European Portuguese (pt-PT). Keep structural JSON keys in English.

OUTPUT ONLY VALID JSON strictly matching this format:
{"objects": [{"name": "NomeDoObjeto", "fields": [{"name": "nome_do_campo", "type": "string"}]}]}

REQUIREMENTS:

- Each object must:
  • include business logic fields
  • include financial/operational attributes
  • include lifecycle fields (estado, tipo, fase)
  • include audit fields (created_at, updated_at, ativo)

- You MUST:
  • create support entities (Categoria, LogSistema, Historico, Configuracao)
  • enrich beyond the plan

- MINIMUM:
  • 6–10 fields per object

DO NOT generate trivial schemas. Return ONLY JSON.
"""


VALID_TYPES = {"string", "integer", "float", "boolean", "date", "datetime", "text"}


# =========================
# DOMAIN BASE
# =========================

BASE_FIELDS = [
    ("ativo", "boolean"),
    ("created_at", "datetime"),
    ("updated_at", "datetime")
]


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


def _infer_type(field_name: str) -> str:
    fn = field_name.lower()

    if any(k in fn for k in ["preco", "valor", "total", "custo", "iva", "saldo"]):
        return "float"
    if any(k in fn for k in ["id", "numero", "quantidade", "stock", "count"]):
        return "integer"
    if "data" in fn or "date" in fn:
        return "datetime"
    if any(k in fn for k in ["ativo", "flag", "estado", "validado"]):
        return "boolean"
    if any(k in fn for k in ["descricao", "observacoes", "notas"]):
        return "text"

    return "string"


# =========================
# ENRICHMENT
# =========================

def _enrich_object(obj: dict, domain: str = "") -> dict:
    existing = {f["name"] for f in obj["fields"]}

    # base fields
    for fname, ftype in BASE_FIELDS:
        if fname not in existing:
            obj["fields"].append({"name": fname, "type": ftype})
            existing.add(fname)

    # domain-specific enrichment
    extra = []

    if domain == "hospital":
        extra = [("numero_processo", "string"), ("seguro", "string")]
    elif domain == "ecommerce":
        extra = [("classificacao", "float"), ("numero_vendas", "integer")] # Traduzido rating para classificacao
    elif domain == "finance":
        extra = [("iban", "string"), ("saldo", "float")]

    for fname, ftype in extra:
        if fname not in existing:
            obj["fields"].append({"name": fname, "type": ftype})

    return obj


# =========================
# NORMALIZATION
# =========================

def _normalize_output(data: dict, domain: str = "") -> dict:
    objects = data.get("objects", [])
    result = []

    for obj in objects:
        name = obj.get("name", "").strip()
        if not name:
            continue

        pk = name.lower() + "id"

        fields = [{"name": pk, "type": "integer"}]
        seen = {pk}

        for f in obj.get("fields", []):
            fname = f.get("name", "").lower().strip()
            if not fname or fname in seen:
                continue

            if fname.endswith("id") and fname != pk:
                continue

            ftype = f.get("type", "string")
            if ftype not in VALID_TYPES:
                ftype = _infer_type(fname)

            fields.append({"name": fname, "type": ftype})
            seen.add(fname)

        obj_clean = {"name": name, "fields": fields}
        obj_clean = _enrich_object(obj_clean, domain)

        result.append(obj_clean)

    return {"objects": result}


# =========================
# SUPPORT ENTITIES
# =========================

def _add_support_entities(data: dict):
    existing = {o["name"].lower() for o in data["objects"]}

    support = ["Categoria", "LogSistema", "Historico", "Configuracao"]

    for name in support:
        if name.lower() not in existing:
            obj = {
                "name": name,
                "fields": [
                    {"name": name.lower() + "id", "type": "integer"},
                    {"name": "nome", "type": "string"},
                    {"name": "descricao", "type": "text"},
                    {"name": "created_at", "type": "datetime"}
                ]
            }
            data["objects"].append(obj)

    return data


# =========================
# FALLBACK
# =========================

def _fallback(plan: dict) -> dict:
    objects = []

    for entity in plan.get("entities", []):
        name = entity.get("name", "")
        if not name:
            continue

        pk = name.lower() + "id"

        fields = [{"name": pk, "type": "integer"}]

        for f in entity.get("suggested_fields", []):
            fname = f.lower().replace(" ", "_")
            fields.append({"name": fname, "type": _infer_type(fname)})

        objects.append({"name": name, "fields": fields})

    data = {"objects": objects}
    return _add_support_entities(data)


# =========================
# MAIN
# =========================

def generate_objects(plan: dict) -> dict:
    domain = plan.get("domain", "")
    
    #  Extrair apenas o bloco de entidades para não sobrecarregar o LLM
    entities = plan.get("entities", [])

    payload = {
        "model": MODELS["generator_objects"],
        "prompt": f"{SYSTEM_PROMPT}\n\nEntities to generate:\n{json.dumps(entities, indent=2, ensure_ascii=False)}",
        "format": "json", # OBRIGA a ser JSON
        "stream": False,
        "options": OPTIONS # Usa as opções de RAM seguras (sem o num_predict de 2048)
    }

    try:
        res = requests.post(OLLAMA_URL, json=payload, timeout=180) # Menos timeout, porque em JSON ele é mais rápido
        res.raise_for_status()

        raw = res.json().get("response", "")
        
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = _extract_json(raw)

        if not data or "objects" not in data:
            print("[generator_objects] fallback LLM")
            return _fallback(plan)

        data = _normalize_output(data, domain)
        data = _add_support_entities(data)

        print(f"[generator_objects] {len(data['objects'])} objetos gerados")
        return data

    except Exception as e:
        print(f"[generator_objects] erro: {e}")
        return _fallback(plan)
