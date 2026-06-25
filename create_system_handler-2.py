# ===== handlers/create_system_handler.py =====
# Handler principal para a rota CREATE_SYSTEM.
# Executa o pipeline completo:
# Cache → Planner → Generators → Aggregator → Semantic Rules
# → Canonical Schema → Validator → Evaluator → Storage.

import time
import json
from concurrent.futures import ThreadPoolExecutor

from planner import generate_plan
from generator_objects import generate_objects
from generator_relations import generate_relations
from generator_workspaces import generate_workspaces
from generator_actions import generate_actions

from aggregator import aggregate_blueprint
from validator import validate_and_fix
from evaluator import evaluate_schema
from semantic_rules import apply_semantic_rules

from cache import verificar_cache, guardar_na_cache
from canonical_schema import apply_canonical_schema
from storage import save_schema


# tentativa inicial + 1 retry
MAX_RETRIES = 2


def log(message: str):
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {message}"
    print(line)
    try:
        with open("pipeline.log", "a", encoding="utf-8") as f:
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
            return {
                k: sanitize_blueprint_safely(v, seen.copy())
                for k, v in data.items()
            }

        if isinstance(data, list):
            return [
                sanitize_blueprint_safely(v, seen.copy())
                for v in data
            ]

    return data


def safe_call(name, func, *args):
    """
    Executa um componente do pipeline de forma segura.
    Se o componente falhar, devolve {} para não rebentar o pipeline completo.
    """
    try:
        return func(*args)
    except Exception as e:
        log(f"[{name}] erro: {e}")
        return {}


def handle_create_system(prompt: str):
    total_start = time.time()
    original_prompt = prompt

    # =========================
    # CACHE
    # =========================

    cached, similarity = verificar_cache(prompt)

    if cached:
        total_time = round(time.time() - total_start, 2)
        log(f"[cache] HIT (similaridade: {similarity})")

        cached_schema = cached.get("schema")
        cached_evaluation = cached.get("evaluation")

        return {
            "success": True,
            "cached": True,
            "type": "SYSTEM",
            "schema": cached_schema,
            "data": cached_schema,
            "evaluation": cached_evaluation,
            "execution_time": total_time,
        }

    log("[cache] MISS")

    # =========================
    # PIPELINE COM RETRY
    # =========================

    for attempt in range(1, MAX_RETRIES + 1):
        log(f"[pipeline] tentativa {attempt}/{MAX_RETRIES}")

        try:
            # =========================
            # PLANNER
            # =========================

            plan = generate_plan(prompt)

            if not plan:
                log("[planner] erro ao gerar plano")
                continue

            # =========================
            # GENERATORS
            # =========================
            # objects e actions podem correr em paralelo.
            # relations e workspaces dependem dos objects finais,
            # por isso correm depois.

            with ThreadPoolExecutor(max_workers=2) as executor:
                future_objects = executor.submit(
                    safe_call,
                    "generator_objects",
                    generate_objects,
                    plan
                )

                future_actions = executor.submit(
                    safe_call,
                    "generator_actions",
                    generate_actions,
                    plan
                )

                objects = future_objects.result()
                actions = future_actions.result()

            relations = safe_call(
                "generator_relations",
                generate_relations,
                plan,
                objects
            )

            workspaces = safe_call(
                "generator_workspaces",
                generate_workspaces,
                plan,
                objects
            )

            # =========================
            # AGGREGATION + NORMALIZAÇÃO
            # =========================

            schema = aggregate_blueprint(
                objects,
                relations,
                actions,
                workspaces
            )

            schema = apply_semantic_rules(schema)
            schema = apply_canonical_schema(schema)

            # =========================
            # VALIDATION + EVALUATION
            # =========================

            validated = validate_and_fix(schema)
            evaluation = evaluate_schema(validated, original_prompt)

            # =========================
            # SUCCESS
            # =========================

            if evaluation.get("valid", False):
                total_time = round(time.time() - total_start, 2)

                safe_validated = sanitize_blueprint_safely(validated)
                safe_evaluation = sanitize_blueprint_safely(evaluation)

                guardar_na_cache(
                    original_prompt,
                    {
                        "schema": safe_validated,
                        "evaluation": safe_evaluation
                    },
                    safe_evaluation,
                )

                save_schema(
                    safe_validated,
                    original_prompt,
                    safe_evaluation,
                    total_time
                )

                log(f"[pipeline] sucesso ({total_time}s)")

                return {
                    "success": True,
                    "cached": False,
                    "type": "SYSTEM",
                    "schema": safe_validated,
                    "data": safe_validated,
                    "evaluation": safe_evaluation,
                    "execution_time": total_time,
                }

            # =========================
            # RETRY COM FEEDBACK
            # =========================

            issues = evaluation.get("issues", [])
            warnings = evaluation.get("warnings", [])
            feedback = evaluation.get("feedback_for_planner", "")

            log(f"[retry] schema rejeitado: {issues}")

            prompt = (
                original_prompt
                + "\n\nO schema anterior foi rejeitado."
                + "\nCorrige os seguintes problemas:"
                + f"\nIssues: {issues}"
                + f"\nWarnings: {warnings}"
                + f"\nFeedback: {feedback}"
            )

        except Exception as e:
            log(f"[pipeline] erro: {e}")

    # =========================
    # FAILURE
    # =========================

    total_time = round(time.time() - total_start, 2)
    log(f"[pipeline] falhou ({total_time}s)")

    return {
        "success": False,
        "type": "SYSTEM",
        "error": "Pipeline falhou após retries",
        "execution_time": total_time,
    }
