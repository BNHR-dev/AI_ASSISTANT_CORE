from app.engine.planner_service import build_execution_plan


def test_build_plan_from_explain_plus_code():
    decision = {
        "task_type": "explain_basic",
        "primary_agent": "AGENT_PROF_IA",
        "selected_model": "qwen3:8b",
        "selected_tool": None,
        "output_format": "définition + image mentale + exemple concret",
        "needs_web": False,
        "second_call": "build",
    }

    plan = build_execution_plan(decision, "Explique les embeddings avec code")
    assert plan.strategy == "two_step_llm"
    assert len(plan.steps) == 2
    assert plan.steps[0].step_type == "llm_primary"
    assert plan.steps[1].step_type == "llm_secondary"


def test_build_plan_from_web_research():
    decision = {
        "task_type": "web_research",
        "primary_agent": "AGENT_PROF_IA",
        "selected_model": "qwen3:14b",
        "selected_tool": "web",
        "output_format": "synthèse + sources utiles + résumé clair",
        "needs_web": True,
        "second_call": None,
    }

    plan = build_execution_plan(decision, "Cherche les dernières news IA")
    assert plan.strategy == "web_pipeline"
    assert [s.step_type for s in plan.steps] == ["tool_web_search", "llm_synthesis"]


def test_build_plan_single_step():
    decision = {
        "task_type": "explain_basic",
        "primary_agent": "AGENT_PROF_IA",
        "selected_model": "qwen3:8b",
        "selected_tool": None,
        "output_format": "définition + image mentale + exemple concret",
        "needs_web": False,
        "second_call": None,
    }

    plan = build_execution_plan(decision, "Explique ce qu'est un embedding.")
    assert plan.strategy == "single_step"
    assert len(plan.steps) == 1
    assert plan.steps[0].step_type == "llm_primary"
    assert plan.steps[0].step_id == "step_primary"


def test_build_plan_visual_pipeline():
    decision = {
        "task_type": "image_generation",
        "primary_agent": "AGENT_CREATIVE_IA",
        "selected_model": "qwen3:8b",
        "selected_tool": "comfyui",
        "output_format": "structured_prompt + visual_parameters",
        "needs_web": False,
        "second_call": None,
    }

    plan = build_execution_plan(decision, "Génère une image d'un robot futuriste.")
    assert plan.strategy == "visual_pipeline"
    assert [s.step_type for s in plan.steps] == ["prepare_visual", "tool_comfyui"]
    assert plan.steps[1].depends_on == ["step_prepare_visual"]
