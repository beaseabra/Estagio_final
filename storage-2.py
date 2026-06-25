import json
import re
import time

from pathlib import Path
from datetime import datetime

from config import PATHS


BASE_DIR = Path(PATHS["schemas"])

BASE_DIR.mkdir(parents=True, exist_ok=True)


# =========================
# CLEAN FILE NAME
# =========================

def _clean_name(text: str):

    text = text.lower()

    stopwords = [

        "quero",
        "cria",
        "criar",

        "um",
        "uma",

        "sistema",
        "de",
        "com",
        "para",
        "e",

        "gestao",
        "gestão",
        "gerir"
    ]

    words = text.split()

    words = [
        w
        for w in words
        if w not in stopwords
    ]

    if not words:
        return "schema"

    name = "_".join(words[:3])

    name = re.sub(
        r"[^a-z0-9_]",
        "",
        name
    )

    return name or "schema"


# =========================
# SAVE SCHEMA
# =========================

def save_schema(
    schema: dict,
    prompt: str,
    evaluation: dict,
    execution_time: float
):

    timestamp = datetime.now().strftime(
        "%Y%m%d_%H%M%S"
    )

    base_name = _clean_name(prompt)

    file_name = f"{base_name}_{timestamp}.json"

    file_path = BASE_DIR / file_name

    payload = {

        "timestamp": time.time(),

        "prompt": prompt,

        "execution_time": execution_time,

        "evaluation": evaluation,

        "schema": schema
    }

    with open(
        file_path,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            payload,
            f,
            indent=2,
            ensure_ascii=False
        )

    print(
        f"[storage] schema guardado: {file_name}"
    )

    return str(file_path)


# =========================
# LIST SCHEMAS
# =========================

def list_schemas():

    files = list(
        BASE_DIR.glob("*.json")
    )

    results = []

    for file in files:

        try:

            with open(
                file,
                "r",
                encoding="utf-8"
            ) as f:

                data = json.load(f)

            results.append({

                "file": file.name,

                "path": str(file),

                "timestamp": data.get(
                    "timestamp",
                    0
                ),

                "prompt": data.get(
                    "prompt",
                    ""
                ),

                "score": data.get(
                    "evaluation",
                    {}
                ).get("score", 0)
            })

        except:
            continue

    results.sort(
        key=lambda x: x["timestamp"],
        reverse=True
    )

    return results


# =========================
# LOAD SCHEMA
# =========================

def load_schema(file_name: str):

    file_path = BASE_DIR / file_name

    if not file_path.exists():
        return None

    try:

        with open(
            file_path,
            "r",
            encoding="utf-8"
        ) as f:

            return json.load(f)

    except:

        return None
