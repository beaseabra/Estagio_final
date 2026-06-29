# ===== handlers/create_object_handler.py =====
import re
import time


VALID_TYPES = {"string", "integer", "float", "boolean", "date", "datetime", "text"}


# =========================
# UTILS
# =========================

def _normalize_name(name: str) -> str:
    name = str(name).strip()
    name = re.sub(r"\s+", " ", name)
    return name


def _normalize_field_name(name: str) -> str:
    name = str(name).strip().lower()

    replacements = {
        "ç": "c",
        "á": "a",
        "à": "a",
        "â": "a",
        "ã": "a",
        "é": "e",
        "ê": "e",
        "í": "i",
        "ó": "o",
        "ô": "o",
        "õ": "o",
        "ú": "u",
    }

    for old, new in replacements.items():
        name = name.replace(old, new)

    name = re.sub(r"[\s\-]+", "_", name)
    name = re.sub(r"[^a-z0-9_]", "", name)
    name = re.sub(r"_+", "_", name)
    return name.strip("_")


def _canonical_token(name: str) -> str:
    name = _normalize_field_name(name)
    return re.sub(r"[^a-z0-9]", "", name)


def _infer_type(field_name: str) -> str:
    fn = field_name.lower()

    if any(k in fn for k in ["preco", "valor", "total", "custo", "saldo", "orcamento", "orçamento"]):
        return "float"

    if any(k in fn for k in ["quantidade", "stock", "numero", "ano", "idade"]):
        return "integer"

    if any(k in fn for k in ["data", "date"]):
        return "datetime"

    if any(k in fn for k in ["ativo", "validado", "aprovado", "pago", "flag"]):
        return "boolean"

    if any(k in fn for k in ["descricao", "observacoes", "notas", "comentario"]):
        return "text"

    return "string"


def _split_fields(raw_fields: str) -> list:
    raw_fields = str(raw_fields or "")

    # normalizar separadores comuns
    raw_fields = raw_fields.replace(" e ", ",")
    raw_fields = raw_fields.replace(";", ",")
    raw_fields = raw_fields.replace("\n", ",")

    parts = [p.strip() for p in raw_fields.split(",") if p.strip()]
    fields = []

    for part in parts:
        field_name = part
        field_type = None

        if ":" in part:
            chunks = part.split(":", 1)
            field_name = chunks[0].strip()
            possible_type = chunks[1].strip().lower()
            if possible_type in VALID_TYPES:
                field_type = possible_type

        else:
            match = re.match(
                r"(.+?)\s+(string|integer|float|boolean|date|datetime|text)$",
                part,
                re.IGNORECASE
            )

            if match:
                field_name = match.group(1).strip()
                possible_type = match.group(2).strip().lower()
                if possible_type in VALID_TYPES:
                    field_type = possible_type

        normalized = _normalize_field_name(field_name)

        if not normalized:
            continue

        fields.append({
            "name": normalized,
            "type": field_type or _infer_type(normalized)
        })

    return fields


def _extract_object_name(prompt: str) -> str:
    prompt = str(prompt or "").strip()

    patterns = [
        r"cria\s+um\s+objeto\s+([A-Za-zÀ-ÿ0-9_ -]+?)\s+com\s+os\s+campos",
        r"cria\s+o\s+objeto\s+([A-Za-zÀ-ÿ0-9_ -]+?)\s+com\s+os\s+campos",
        r"criar\s+objeto\s+([A-Za-zÀ-ÿ0-9_ -]+?)\s+com\s+os\s+campos",
        r"gera\s+um\s+objeto\s+([A-Za-zÀ-ÿ0-9_ -]+?)\s+com\s+os\s+campos",
        r"gerar\s+objeto\s+([A-Za-zÀ-ÿ0-9_ -]+?)\s+com\s+os\s+campos",
    ]

    for pattern in patterns:
        match = re.search(pattern, prompt, re.IGNORECASE)

        if match:
            return _normalize_name(match.group(1))

    match = re.search(r"objeto\s+([A-Za-zÀ-ÿ0-9_ -]+)", prompt, re.IGNORECASE)
    if match:
        name = match.group(1)
        name = re.split(r"\bcom\b|\bcampos\b", name, flags=re.IGNORECASE)[0]
        return _normalize_name(name)

    return "Objeto"


def _extract_fields(prompt: str) -> list:
    prompt = str(prompt or "").strip()

    patterns = [
        r"com\s+os\s+campos\s+(.+)$",
        r"com\s+campos\s+(.+)$",
        r"campos\s*:\s*(.+)$",
        r"fields\s*:\s*(.+)$",
    ]

    for pattern in patterns:
        match = re.search(pattern, prompt, re.IGNORECASE)

        if match:
            return _split_fields(match.group(1))

    return []


def _build_object(prompt: str) -> dict:
    object_name = _extract_object_name(prompt)
    field_candidates = _extract_fields(prompt)

    pk_name = _canonical_token(object_name) + "id"

    fields = [{"name": pk_name, "type": "integer"}]
    seen = {pk_name}

    for field in field_candidates:
        fname = field.get("name", "")
        ftype = field.get("type", "string")

        if not fname or fname in seen:
            continue

        if fname.endswith("id") and fname != pk_name:
            continue

        if ftype not in VALID_TYPES:
            ftype = _infer_type(fname)

        fields.append({
            "name": fname,
            "type": ftype
        })

        seen.add(fname)

    return {
        "name": object_name,
        "fields": fields
    }


# =========================
# MAIN
# =========================

def handle_create_object(prompt: str):
    print(f"[create_object] A gerar objeto rápido para: '{prompt}'...")
    start = time.time()

    try:
        obj = _build_object(prompt)

        schema = {
            "objects": [obj],
            "relations": [],
            "actions": [],
            "workspaces": [],
            "metadata": {
                "total_objects": 1,
                "total_relations": 0,
                "total_actions": 0,
                "total_workspaces": 0
            }
        }

        total_time = round(time.time() - start, 2)

        print(f"[create_object] objeto gerado: {obj['name']} com {len(obj['fields'])} campos")

        return {
            "success": True,
            "type": "OBJECT",
            "schema": schema,
            "data": schema,
            "object": obj,
            "execution_time": total_time
        }

    except Exception as e:
        print(f"[create_object] erro: {e}")
        return {
            "success": False,
            "type": "OBJECT",
            "error": f"Ocorreu um erro ao gerar o objeto: {e}"
        }
