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
