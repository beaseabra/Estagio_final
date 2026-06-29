# ===== cache.py =====

import json
import os
import time
import shutil
import numpy as np
import requests
from scipy.spatial.distance import cosine

from config import (
    MODELS, EMBEDDING_URL, CACHE_SIMILARITY_THRESHOLD, PATHS
)

CACHE_DIR = "database"
if not os.path.exists(CACHE_DIR):
    os.makedirs(CACHE_DIR)
CACHE_FILE = os.path.join(CACHE_DIR, "prompt_history.json")


def _extract_entities(prompt: str):
    prompt = prompt.lower()
    mapping = {
        "produto": "Produto", "cliente": "Cliente", "fornecedor": "Fornecedor",
        "encomenda": "Encomenda", "stock": "Stock", "pagamento": "Pagamento",
        "consulta": "Consulta", "paciente": "Paciente", "medico": "Medico",
        "reserva": "Reserva"
    }
    found = set()
    for word, entity in mapping.items():
        if word in prompt: found.add(entity)
    return sorted(list(found))

def _embed(text: str):
    try:
        response = requests.post(
            EMBEDDING_URL,
            json={"model": MODELS["embeddings"], "input": text}
        )
        response.raise_for_status()
        embeddings = response.json().get("embeddings", [])
        return embeddings[0] if embeddings else None
    except Exception as e:
        print(f"[cache] erro embedding: {e}")
        return None

def _load():
    if not os.path.exists(CACHE_FILE): return []
    try:
        with open(CACHE_FILE, "r") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except:
        return []

def _save(data):
    tmp = CACHE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    shutil.move(tmp, CACHE_FILE)


def _schema_contains_entities(schema: dict, entities: list):
    object_names = [obj.get("name", "").lower() for obj in schema.get("objects", [])]
    for entity in entities:
        if entity.lower() not in object_names:
            return False
    return True

def _lexical_similarity(prompt1: str, prompt2: str) -> float:
    words1 = set(prompt1.lower().split())
    words2 = set(prompt2.lower().split())
    if not words1 or not words2: return 0.0
    intersection = words1.intersection(words2)
    return len(intersection) / max(len(words1), len(words2))


def verificar_cache(prompt: str):
    cache = _load()
    if not cache: return None, 0

    emb_new = _embed(prompt)
    if emb_new is None: return None, 0

    emb_new = np.array(emb_new)
    entities_new = _extract_entities(prompt)

    best_score = 0
    best_data = None

    for item in cache:
        try:
            emb_old = np.array(item["embedding"])
            entities_old = item.get("entities", [])
            old_prompt = item.get("prompt", "")

            if set(entities_old) != set(entities_new): continue
            if emb_old.shape != emb_new.shape: continue

            score = 1 - cosine(emb_new, emb_old)

            if score >= CACHE_SIMILARITY_THRESHOLD:
                lex_score = _lexical_similarity(prompt, old_prompt)
                
                if prompt.strip().lower() != old_prompt.strip().lower() and lex_score < 0.75:
                    continue

                schema = item["data"].get("schema", {})
                if not _schema_contains_entities(schema, entities_new): continue

                if score > best_score:
                    best_score = score
                    best_data = item["data"]
        except:
            continue

    if best_score >= CACHE_SIMILARITY_THRESHOLD:
        print(f"[cache] HIT ({best_score*100:.1f}%)")
        return best_data, best_score

    print("[cache] MISS")
    return None, 0

def guardar_na_cache(prompt: str, data: dict, evaluation: dict):
    cache = _load()
    emb = _embed(prompt)
    if emb is None: return
    entities = _extract_entities(prompt)

    cache.append({
        "timestamp": time.time(),
        "prompt": prompt,
        "embedding": emb,
        "entities": entities,
        "evaluation": evaluation,
        "data": data
    })

    if len(cache) > 200: cache = cache[-200:]
    _save(cache)
    print("[cache] schema guardado no histórico de performance")
