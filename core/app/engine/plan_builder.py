from __future__ import annotations

import os

from app.engine.blender_model_config import get_blender_llm_model
from app.engine.planner_types import ExecutionPlan, PlanStep

TOOL_RETRY_MAX_ATTEMPTS_ENV = "AAC_TOOL_RETRY_MAX_ATTEMPTS"


def _tool_max_attempts() -> int:
    """Tentatives des steps outils (tool_*) sur erreur. Défaut 1 = aucun
    retry (comportement historique) : un outil GPU qui échoue coûte cher à
    rejouer aveuglément, l'opt-in est explicite. Borné à [1, 5]."""
    raw = (os.getenv(TOOL_RETRY_MAX_ATTEMPTS_ENV) or "1").strip()
    try:
        return min(5, max(1, int(raw)))
    except ValueError:
        return 1


def build_plan_from_decision(decision: dict, message: str) -> ExecutionPlan:
    task_type = decision["task_type"]
    primary_agent = decision["primary_agent"]
    selected_model = decision["selected_model"]
    selected_tool = decision["selected_tool"]
    output_format = decision["output_format"]
    second_call = decision.get("second_call")

    steps: list[PlanStep] = []

    if selected_tool == "blender":
        steps.append(
            PlanStep(
                step_id="step_prepare_blender",
                step_type="prepare_blender_script",
                goal="Générer le script bpy via Ollama et préparer la BlenderRequest",
                agent="AGENT_BUILDER_IA",
                model=get_blender_llm_model(),
            )
        )
        steps.append(
            PlanStep(
                step_id="step_run_blender",
                step_type="tool_blender",
                goal="Exécuter Blender en background et produire le fichier .blend",
                tool="blender",
                depends_on=["step_prepare_blender"],
                max_attempts=_tool_max_attempts(),
            )
        )
        return ExecutionPlan(
            task_type=task_type,
            steps=steps,
            strategy="blender_pipeline",
        )

    if selected_tool == "comfyui":
        steps.append(
            PlanStep(
                step_id="step_prepare_visual",
                step_type="prepare_visual",
                goal="Préparer la demande visuelle",
                agent=primary_agent,
                model=selected_model,
                output_format="prompt visuel structuré",
            )
        )
        steps.append(
            PlanStep(
                step_id="step_run_comfyui",
                step_type="tool_comfyui",
                goal="Exécuter le workflow ComfyUI",
                tool="comfyui",
                depends_on=["step_prepare_visual"],
                max_attempts=_tool_max_attempts(),
            )
        )
        return ExecutionPlan(
            task_type=task_type,
            steps=steps,
            strategy="visual_pipeline",
        )

    if decision.get("needs_web"):
        steps.append(
            PlanStep(
                step_id="step_web_search",
                step_type="tool_web_search",
                goal="Récupérer des résultats web",
                tool="web",
                max_attempts=_tool_max_attempts(),
            )
        )
        steps.append(
            PlanStep(
                step_id="step_web_synthesis",
                step_type="llm_synthesis",
                goal="Synthétiser les résultats web",
                agent=primary_agent,
                model=selected_model,
                output_format=output_format,
                depends_on=["step_web_search"],
            )
        )
        return ExecutionPlan(
            task_type=task_type,
            steps=steps,
            strategy="web_pipeline",
        )

    steps.append(
        PlanStep(
            step_id="step_primary",
            step_type="llm_primary",
            goal="Traiter la tâche principale",
            agent=primary_agent,
            model=selected_model,
            output_format=output_format,
        )
    )

    if second_call:
        steps.append(
            PlanStep(
                step_id="step_secondary",
                step_type="llm_secondary",
                goal="Compléter la réponse par une seconde étape spécialisée",
                depends_on=["step_primary"],
                meta={"requested_task_type": second_call},
            )
        )
        return ExecutionPlan(
            task_type=task_type,
            steps=steps,
            strategy="two_step_llm",
        )

    return ExecutionPlan(
        task_type=task_type,
        steps=steps,
        strategy="single_step",
    )