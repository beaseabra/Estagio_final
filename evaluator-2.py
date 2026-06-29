# ===== evaluator.py =====

from __future__ import annotations

from typing import Union

from models import BlueprintModel, parse_blueprint


WEIGHTS = {
    "structural_completeness": 25,
    "richness": 20,
    "consistency": 20,
    "domain_alignment": 20,
    "business_logic": 15,
}

MINIMUM_PASSING_SCORE = 55


def evaluate_blueprint(
    blueprint: Union[BlueprintModel, dict],
    prompt: str = "",
    operation_type: str = "CREATE_SYSTEM",
) -> dict:

    if isinstance(blueprint, dict):
        bp = parse_blueprint(blueprint)
    elif isinstance(blueprint, BlueprintModel):
        bp = blueprint
    else:
        bp = BlueprintModel()
        
    if operation_type != "CREATE_SYSTEM":
        return {
            "valid": True,
            "score": 100,
            "issues": [],
            "warnings": [],
            "missing_components": [],
            "feedback_for_planner": (
                f"Bypass de avaliação para operação parcial ({operation_type})."
            ),
        }

    objects = bp.objects
    relations = bp.relations
    workspaces = bp.workspaces

    object_names_lower = {obj.name.lower() for obj in objects}

    issues: list[str] = []
    warnings: list[str] = []
    missing_components: list[str] = []

    if isinstance(blueprint, dict):
        val = blueprint.get("_validation") or {}

        hard_val = val.get("hard") or {}
        if isinstance(hard_val, dict):
            raw_errors = hard_val.get("errors") or []
            if isinstance(raw_errors, list):
                issues.extend(str(e) for e in raw_errors if e is not None)

        soft_val = val.get("soft") or {}
        if isinstance(soft_val, dict):
            raw_warnings = soft_val.get("warnings") or []
            if isinstance(raw_warnings, list):
                warnings.extend(str(w) for w in raw_warnings if w is not None)

    # ─────────────────────────────────────────────
    # STRUCTURAL COMPLETENESS (max 25)
    # ─────────────────────────────────────────────

    struct_score = 0

    if len(objects) >= 4:
        struct_score += 12
    elif len(objects) >= 2:
        struct_score += 8
    else:
        issues.append("Too few objects")

    if relations:
        struct_score += min(len(relations) * 2, 8)
    else:
        missing_components.append("relations")

    if workspaces:
        struct_score += min(len(workspaces) * 2, 5)
    else:
        missing_components.append("workspaces")

    struct_score = min(struct_score, 25)

    # ─────────────────────────────────────────────
    # RICHNESS (max 20)
    # ─────────────────────────────────────────────

    rich_score = 0

    if objects:
        avg_fields = sum(len(obj.fields) for obj in objects) / max(len(objects), 1)
        if avg_fields >= 8:
            rich_score += 10
        elif avg_fields >= 5:
            rich_score += 7
        else:
            rich_score += 4

    rich_score += min(len(relations) * 2, 7)

    if len(objects) >= 6:
        rich_score += 3

    rich_score = min(rich_score, 20)

    # ─────────────────────────────────────────────
    # CONSISTENCY (max 20)
    # ─────────────────────────────────────────────

    cons_score = 0


    fk_issues = 0
    for obj in objects:
        for f in obj.fields:
            if f.name.startswith("ref_"):
                target = f.name[4:]  
                if target not in object_names_lower:
                    fk_issues += 1

    if fk_issues == 0:
        cons_score += 8
    elif fk_issues <= 2:
        cons_score += 4

    assigned: set[str] = set()
    for ws in workspaces:
        assigned.update(ws.objects)

    if len(objects) > 0:
        coverage = len(assigned) / len(objects)
        if coverage >= 0.8:
            cons_score += 6

    if len(relations) >= max(len(objects) - 1, 1):
        cons_score += 6

    cons_score = min(cons_score, 20)

    # ─────────────────────────────────────────────
    # DOMAIN ALIGNMENT (max 20)
    # ─────────────────────────────────────────────

    align_score = 10

    if prompt:
        prompt_lower = prompt.lower()
        keywords = [
            "cliente", "produto", "encomenda", "paciente",
            "medico", "livro", "leitor", "biblioteca",
        ]
        matched = sum(
            1 for k in keywords
            if k in prompt_lower and k in object_names_lower
        )
        align_score += matched * 2

    align_score = min(align_score, 20)

    # ─────────────────────────────────────────────
    # BUSINESS LOGIC (max 15)
    # ─────────────────────────────────────────────

    biz_score = 0

    if "encomenda" in object_names_lower and "cliente" in object_names_lower:
        biz_score += 5
    if "encomenda" in object_names_lower and "produto" in object_names_lower:
        biz_score += 4
    if any("log" in n for n in object_names_lower):
        biz_score += 3
    if len(workspaces) >= 3:
        biz_score += 3

    biz_score = min(biz_score, 15)

    # ─────────────────────────────────────────────
    # SCORE FINAL
    # ─────────────────────────────────────────────

    total_score = struct_score + rich_score + cons_score + align_score + biz_score
    total_score -= len(issues) * 3
    total_score -= int(len(warnings) * 0.5)
    total_score = int(max(0, min(total_score, 100)))

    valid = total_score >= MINIMUM_PASSING_SCORE and len(objects) > 0

    feedback: list[str] = []
    if len(objects) < 4:
        feedback.append("Add more entities to increase system depth")
    if len(relations) < max(len(objects) - 1, 1):
        feedback.append("Increase relationships between entities")
    if len(workspaces) < 2:
        feedback.append("Improve workspace organization")
    if issues:
        feedback.append("Fix structural issues before adding complexity")

    feedback_for_planner = (
        " | ".join(feedback) if feedback else "System is strong — refine details"
    )

    print(f"[evaluator] score={total_score}")

    return {
        "valid": valid,
        "score": total_score,
        "issues": issues,
        "warnings": warnings,
        "missing_components": missing_components,
        "feedback_for_planner": feedback_for_planner,
    }


def evaluate_schema(
    schema: Union[BlueprintModel, dict],
    prompt: str = "",
    operation_type: str = "CREATE_SYSTEM",
) -> dict:
    return evaluate_blueprint(schema, prompt, operation_type)
