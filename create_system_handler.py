# ===== handlers/create_system_handler.py =====
# FIX: `from aggregator import aggregate_blueprint` agora é válido —
# o aggregator.py expõe o alias retrocompatível.

import time
import json

from planner import generate_plan
from generator_objects import generate_objects
from generator_relations import generate_relations
from generator_workspaces import generate_workspaces
from generator_actions import generate_actions

# FIX: aggregate_blueprint é agora um alias exportado por aggregator.py
from aggregator import aggregate_blueprint
from validator import validate_and_fix
from evaluator import evaluate_schema
from semantic_rules import apply_semantic_rules

from cache import verificar_cache, guardar_na_cache
from canonical_schema import apply_canonical_schema
from storage import save_schema

MAX_RETRIES = 1


def log(message: str):
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {message}"
    print(line)
    try:
        with open("pipeline.log", "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def sanitize_blueprint_safely(data, seen=None):
    if seen is None:
        seen = set()
    if isinstance(data, (dict, list)):
        obj_id = id(data)
        if obj_id in seen:
            return None
        seen.add(obj_id)
        if isinstance(data, dict):
            return {k: sanitize_blueprint_safely(v, seen.copy()) for k, v in data.items()}
        elif isinstance(data, list):
            return [sanitize_blueprint_safely(v, seen.copy()) for v in data]
    return data


def handle_create_system(prompt: str):
    total_start = time.time()
    original_prompt = prompt

    # Cache
    cached, similarity = verificar_cache(prompt)
    if cached:
        total_time = round(time.time() - total_start, 2)
        log(f"[cache] HIT (similaridade: {similarity})")
        return {
            "success": True,
            "cached": True,
            "schema": cached.get("schema"),
            "evaluation": cached.get("evaluation"),
            "execution_time": total_time,
        }

    log("[cache] MISS")

    for attempt in range(1, MAX_RETRIES + 1):
        log(f"[pipeline] tentativa {attempt}")
        try:
            plan = generate_plan(prompt)
            if not plan:
                log("[planner] erro ao gerar plano")
                continue

            try:
                objects = generate_objects(plan)
            except Exception as e:
                log(f"[generator_objects] erro: {e}")
                objects = {}

            try:
                relations = generate_relations(plan)
            except Exception as e:
                log(f"[generator_relations] erro: {e}")
                relations = {}

            try:
                workspaces = generate_workspaces(plan)
            except Exception as e:
                log(f"[generator_workspaces] erro: {e}")
                workspaces = {}

            try:
                actions = generate_actions(plan)
            except Exception as e:
                log(f"[generator_actions] erro: {e}")
                actions = {}

            # aggregate_blueprint(objects, relations, actions, workspaces) — ordem do alias
            schema = aggregate_blueprint(objects, relations, actions, workspaces)
            schema = apply_semantic_rules(schema)
            schema = apply_canonical_schema(schema)
            validated = validate_and_fix(schema)
            evaluation = evaluate_schema(validated, original_prompt)

            if evaluation.get("valid", False):
                total_time = round(time.time() - total_start, 2)
                safe_validated = sanitize_blueprint_safely(validated)
                safe_evaluation = sanitize_blueprint_safely(evaluation)

                guardar_na_cache(
                    original_prompt,
                    {"schema": safe_validated, "evaluation": safe_evaluation},
                    safe_evaluation,
                )

                save_schema(safe_validated, original_prompt, safe_evaluation, total_time)
                log(f"[pipeline] sucesso ({total_time}s)")
                return {
                    "success": True,
                    "cached": False,
                    "schema": safe_validated,
                    "evaluation": safe_evaluation,
                    "execution_time": total_time,
                }

            issues = evaluation.get("issues", [])
            log(f"[retry] schema rejeitado: {issues}")
            prompt = original_prompt + "\n\nMelhora o sistema anterior corrigindo: " + f"{issues}"

        except Exception as e:
            log(f"[pipeline] erro: {e}")

    total_time = round(time.time() - total_start, 2)
    log(f"[pipeline] falhou ({total_time}s)")
    return {"success": False, "error": "Pipeline falhou após retries"}
