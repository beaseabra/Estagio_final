import time

from planner import generate_plan

from generator_objects import generate_objects
from generator_relations import generate_relations
from generator_workspaces import generate_workspaces
from generator_actions import generate_actions  # 🔥 Importado o gerador de ações

# Alterado de aggregate_schema para aggregate_blueprint para suportar as ações reais
from aggregator import aggregate_blueprint
from validator import validate_and_fix
from evaluator import evaluate_schema
from semantic_rules import apply_semantic_rules

from cache import (
    verificar_cache,
    guardar_na_cache
)

from canonical_schema import (
    apply_canonical_schema
)

from storage import save_schema


MAX_RETRIES = 1  # 🔥 reduzir carga


def log(message: str):

    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")

    line = f"[{timestamp}] {message}"

    print(line)

    try:
        with open("pipeline.log", "a") as f:
            f.write(line + "\n")
    except:
        pass


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

        return {
            "success": True,
            "cached": True,
            "schema": cached.get("schema"),
            "evaluation": cached.get("evaluation"),
            "execution_time": total_time
        }

    log("[cache] MISS")

    # =========================
    # PIPELINE LOOP
    # =========================

    for attempt in range(1, MAX_RETRIES + 1):

        log(f"[pipeline] tentativa {attempt}")

        try:

            # =========================
            # PLANNER
            # =========================

            plan = generate_plan(prompt)

            if not plan:
                log("[planner] erro ao gerar plano")
                continue

            # =========================
            # GENERATORS (SEQUENCIAL)
            # =========================

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

            # 🔥 Chamada inteligente ao gerador de ações reais do LLM
            try:
                actions = generate_actions(plan)
            except Exception as e:
                log(f"[generator_actions] erro: {e}")
                actions = {}

            # =========================
            # AGGREGATOR
            # =========================

            # Agora passamos os 4 elementos reais gerados pelo ecossistema de LLMs
            schema = aggregate_blueprint(
                objects,
                relations,
                actions,  # 🔥 Injetadas as ações dinâmicas aqui
                workspaces
            )

            # =========================
            # POST-PROCESSING & RULES
            # =========================

            schema = apply_semantic_rules(schema)
            schema = apply_canonical_schema(schema)

            # =========================
            # VALIDATOR
            # =========================

            validated = validate_and_fix(schema)

            # =========================
            # EVALUATOR
            # =========================

            evaluation = evaluate_schema(validated, original_prompt)

            if evaluation.get("valid", False):

                # =========================
                # SUCCESS — SAVE & CACHE
                # =========================

                total_time = round(time.time() - total_start, 2)

                guardar_na_cache(
                    original_prompt,
                    {
                        "schema": validated,
                        "evaluation": evaluation
                    },
                    evaluation
                )

                save_schema(
                    validated,
                    original_prompt,
                    evaluation,
                    total_time
                )

                log(f"[pipeline] sucesso ({total_time}s)")

                return {
                    "success": True,
                    "cached": False,
                    "schema": validated,
                    "evaluation": evaluation,
                    "execution_time": total_time
                }

            # =========================
            # RETRY (leve)
            # =========================

            issues = evaluation.get("issues", [])

            log(f"[retry] schema rejeitado: {issues}")

            prompt = (
                original_prompt
                + "\n\nMelhora o sistema anterior corrigindo: "
                + f"{issues}"
            )

        except Exception as e:

            log(f"[pipeline] erro: {e}")

    # =========================
    # FAILED
    # =========================

    total_time = round(time.time() - total_start, 2)

    log(f"[pipeline] falhou ({total_time}s)")

    return {
        "success": False,
        "error": "Pipeline falhou após retries"
    }
