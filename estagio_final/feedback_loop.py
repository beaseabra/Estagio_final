# ===== feedback_loop.py =====

"""
Feedback Loop — drives iterative improvement of blueprint generation.
"""

import time

from planner import generate_plan
from generator_objects import generate_objects
from generator_relations import generate_relations
from generator_actions import generate_actions
from generator_workspaces import generate_workspaces
from aggregator import aggregate_blueprint
from validator import validate_and_fix
from evaluator import evaluate_blueprint


# =========================
# CONFIGURATION
# =========================

SCORE_THRESHOLD = 85
MAX_ITERATIONS = 4
IMPROVEMENT_PATIENCE = 1


def _log(message: str, log_file: str = None):
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] [feedback_loop] {message}"
    print(line)
    if log_file:
        try:
            with open(log_file, "a") as f:
                f.write(line + "\n")
        except Exception:
            pass


# =========================
# FEEDBACK BUILDER
# =========================

def _build_feedback(evaluation: dict, attempt: int) -> dict:
    feedback = {
        "issues": evaluation.get("issues", []),
        "warnings": evaluation.get("warnings", []),
        "suggestions": evaluation.get("missing_components", []),
        "score": evaluation.get("score", 0),
        "attempt": attempt,
        "narrative": evaluation.get("feedback_for_planner", "")
    }

    feedback["full_text"] = f"""
Issues: {feedback['issues']}
Warnings: {feedback['warnings']}
Suggestions: {feedback['suggestions']}
Analysis: {feedback['narrative']}
"""

    return feedback


# =========================
# PIPELINE
# =========================

def _run_pipeline(prompt: str, feedback: dict = None) -> tuple:
    from concurrent.futures import ThreadPoolExecutor

    # Step 1: Plan
    plan = generate_plan(prompt, feedback=feedback if feedback else None)
    if not plan:
        return None, None

    # Step 2: Generate (parallel)
    with ThreadPoolExecutor(max_workers=4) as executor:
        future_objects = executor.submit(generate_objects, plan)
        future_relations = executor.submit(generate_relations, plan)
        future_actions = executor.submit(generate_actions, plan)

        objects_data = future_objects.result()

        future_workspaces = executor.submit(generate_workspaces, plan, objects_data)

        relations_data = future_relations.result()
        actions_data = future_actions.result()
        workspaces_data = future_workspaces.result()

    # Step 3: Aggregate
    blueprint = aggregate_blueprint(
        objects_data,
        relations_data,
        actions_data,
        workspaces_data
    )

    # Step 4: Validate
    blueprint = validate_and_fix(blueprint, prompt)

    # Step 5: Evaluate
    evaluation = evaluate_blueprint(blueprint, prompt)

    return blueprint, evaluation


# =========================
# MAIN LOOP
# =========================

def run_feedback_loop(
    prompt: str,
    initial_blueprint: dict = None,
    initial_evaluation: dict = None,
    log_file: str = None
) -> dict:

    best_blueprint = initial_blueprint
    best_evaluation = initial_evaluation
    best_score = (initial_evaluation or {}).get("score", 0)

    attempts = []
    no_improvement_count = 0
    previous_score = best_score

    # =========================
    # FIRST PASS
    # =========================

    if not initial_blueprint or not initial_evaluation:
        _log("Starting first pipeline pass", log_file)

        blueprint, evaluation = _run_pipeline(prompt)

        if not blueprint or not evaluation:
            return {
                "success": False,
                "blueprint": None,
                "evaluation": None,
                "attempts": 0,
                "improvement": 0
            }

        best_blueprint = blueprint
        best_evaluation = evaluation
        best_score = evaluation.get("score", 0)
        previous_score = best_score

        attempts.append({"attempt": 0, "score": best_score})
        _log(f"First pass score: {best_score}", log_file)

    first_score = best_score

    # PASSOU À PRIMEIRA
    if best_evaluation.get("valid") and best_score >= SCORE_THRESHOLD:
        return {
            "success": True,
            "blueprint": best_blueprint,
            "evaluation": best_evaluation,
            "attempts": 1,
            "improvement": 0
        }

    # =========================
    # FEEDBACK LOOP
    # =========================

    seen_scores = set()

    for iteration in range(1, MAX_ITERATIONS + 1):

        _log(f"Iteration {iteration}/{MAX_ITERATIONS} — score: {best_score}", log_file)

        feedback = _build_feedback(best_evaluation, iteration)

        # 🔥 DETETAR REPETIÇÃO
        if best_score in seen_scores:
            _log("Score repetido — forcing exploration", log_file)
            feedback["force_exploration"] = True

        seen_scores.add(best_score)

        blueprint, evaluation = _run_pipeline(prompt, feedback=feedback)

        if not blueprint or not evaluation:
            _log("Pipeline falhou nesta iteração", log_file)
            continue

        score = evaluation.get("score", 0)
        attempts.append({"attempt": iteration, "score": score})

        _log(f"Iteration {iteration} score: {score}", log_file)

        # Melhor resultado
        if score > best_score:
            best_score = score
            best_blueprint = blueprint
            best_evaluation = evaluation
            no_improvement_count = 0
        else:
            no_improvement_count += 1

        # SUCESSO
        if evaluation.get("valid") and score >= SCORE_THRESHOLD:
            _log(f"Threshold atingido: {score}", log_file)
            break

        # EARLY STOP
        if no_improvement_count >= IMPROVEMENT_PATIENCE and iteration >= 2:
            _log("Sem melhoria — stop", log_file)
            break

        previous_score = score

    # =========================
    # RESULT
    # =========================

    improvement = best_score - first_score
    success = best_evaluation.get("valid", False) and best_score >= SCORE_THRESHOLD

    _log(
        f"Final — score={best_score}, improvement={improvement}, attempts={len(attempts)}",
        log_file
    )

    return {
        "success": success,
        "blueprint": best_blueprint,
        "evaluation": best_evaluation,
        "attempts": len(attempts),
        "improvement": improvement,
        "attempt_log": attempts
    }
