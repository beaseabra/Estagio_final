# ===== generator_objects.py =====

import json
import re
import requests

from config import MODELS, OPTIONS, OLLAMA_URL


# =========================
# PROMPT MELHORADO
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
    except Exception:
        return None


def _canonical_token(name: str) -> str:
    """
    Converte nomes de objetos em tokens seguros para PKs.
    Exemplo:
        "Ordem Producao" -> "ordemproducao"
        "Máquina CNC"    -> "mquinacnc" se vier com acento
    """
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def _normalize_field_name(name: str) -> str:
    """
    Normaliza nomes de campos para snake_case simples.
    """
    name = str(name).strip().lower()
    name = re.sub(r"[\s\-]+", "_", name)
    name = re.sub(r"[^a-z0-9_]", "", name)
    return name


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
    existing = {f["name"] for f in obj.get("fields", []) if isinstance(f, dict)}

    # Base fields
    for fname, ftype in BASE_FIELDS:
        if fname not in existing:
            obj["fields"].append({"name": fname, "type": ftype})
            existing.add(fname)

    # Domain-specific enrichment
    extra = []

    if domain == "hospital":
        extra = [
            ("numero_processo", "string"),
            ("seguro", "string")
        ]

    elif domain == "ecommerce":
        extra = [
            ("classificacao", "float"),
            ("numero_vendas", "integer")
        ]

    elif domain == "finance":
        extra = [
            ("iban", "string"),
            ("saldo", "float")
        ]

    for fname, ftype in extra:
        if fname not in existing:
            obj["fields"].append({"name": fname, "type": ftype})
            existing.add(fname)

    return obj


# =========================
# NORMALIZATION
# =========================

def _normalize_output(data: dict, domain: str = "") -> dict:
    objects = data.get("objects", [])

    if not isinstance(objects, list):
        objects = []

    result = []

    for obj in objects:
        if not isinstance(obj, dict):
            continue

        name = str(obj.get("name", "")).strip()
        if not name:
            continue

        pk = _canonical_token(name) + "id"

        fields = [{"name": pk, "type": "integer"}]
        seen = {pk}

        raw_fields = obj.get("fields", [])
        if not isinstance(raw_fields, list):
            raw_fields = []

        for f in raw_fields:
            if not isinstance(f, dict):
                continue

            fname = _normalize_field_name(f.get("name", ""))

            if not fname or fname in seen:
                continue

            # Evita IDs inventados pelo LLM que não sejam a PK canónica
            if fname.endswith("id") and fname != pk:
                continue

            ftype = str(f.get("type", "string")).lower().strip()

            if ftype not in VALID_TYPES:
                ftype = _infer_type(fname)

            fields.append({"name": fname, "type": ftype})
            seen.add(fname)

        obj_clean = {
            "name": name,
            "fields": fields
        }

        obj_clean = _enrich_object(obj_clean, domain)
        result.append(obj_clean)

    return {"objects": result}


# =========================
# SUPPORT ENTITIES
# =========================

def _add_support_entities(data: dict):
    objects = data.get("objects", [])

    if not isinstance(objects, list):
        objects = []

    data["objects"] = objects

    existing = {
        str(o.get("name", "")).lower()
        for o in objects
        if isinstance(o, dict)
    }

    support = [
        "Categoria",
        "LogSistema",
        "Historico",
        "Configuracao"
    ]

    for name in support:
        if name.lower() not in existing:
            obj = {
                "name": name,
                "fields": [
                    {"name": _canonical_token(name) + "id", "type": "integer"},
                    {"name": "nome", "type": "string"},
                    {"name": "descricao", "type": "text"},
                    {"name": "created_at", "type": "datetime"},
                    {"name": "updated_at", "type": "datetime"},
                    {"name": "ativo", "type": "boolean"}
                ]
            }

            data["objects"].append(obj)

    return data


# =========================
# FALLBACK
# =========================

def _fallback(plan: dict) -> dict:
    objects = []

    entities = plan.get("entities", [])

    if not isinstance(entities, list):
        entities = []

    for entity in entities:
        if not isinstance(entity, dict):
            continue

        name = str(entity.get("name", "")).strip()

        if not name:
            continue

        pk = _canonical_token(name) + "id"

        fields = [{"name": pk, "type": "integer"}]
        seen = {pk}

        suggested_fields = entity.get("suggested_fields", [])

        if not isinstance(suggested_fields, list):
            suggested_fields = []

        for f in suggested_fields:
            if isinstance(f, dict):
                f = f.get("name", "")

            fname = _normalize_field_name(f)

            if not fname or fname in seen:
                continue

            if fname.endswith("id") and fname != pk:
                continue

            fields.append({
                "name": fname,
                "type": _infer_type(fname)
            })

            seen.add(fname)

        obj_clean = {
            "name": name,
            "fields": fields
        }

        obj_clean = _enrich_object(obj_clean, plan.get("domain", ""))
        objects.append(obj_clean)

    data = {"objects": objects}
    return _add_support_entities(data)


# =========================
# MAIN
# =========================

def generate_objects(plan: dict) -> dict:
    domain = plan.get("domain", "")

    # Extrair apenas o bloco de entidades para não sobrecarregar o LLM
    entities = plan.get("entities", [])

    if not isinstance(entities, list):
        entities = []

    payload = {
        "model": MODELS["generator_objects"],
        "prompt": (
            f"{SYSTEM_PROMPT}\n\n"
            f"Entities to generate:\n"
            f"{json.dumps(entities, indent=2, ensure_ascii=False)}"
        ),
        "format": "json",
        "stream": False,
        "options": OPTIONS
    }

    try:
        res = requests.post(
            OLLAMA_URL,
            json=payload,
            timeout=180
        )

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
