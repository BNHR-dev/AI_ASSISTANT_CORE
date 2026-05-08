"""
Tests : blender_script + selected_tool blender → blender_pipeline
avec les steps corrects et la dépendance attendue.
"""
from app.engine.plan_builder import build_plan_from_decision


def _make_decision(task_type="blender_script", selected_tool="blender"):
    return {
        "task_type": task_type,
        "primary_agent": "AGENT_BUILDER_IA",
        "selected_model": "qwen2.5-coder:7b",
        "selected_tool": selected_tool,
        "output_format": "blender_script",
        "needs_web": False,
        "second_call": None,
    }


def test_blender_pipeline_strategy():
    plan = build_plan_from_decision(_make_decision(), "crée une scène Blender")
    assert plan.strategy == "blender_pipeline"


def test_blender_pipeline_has_two_steps():
    plan = build_plan_from_decision(_make_decision(), "crée une scène Blender")
    assert len(plan.steps) == 2


def test_blender_pipeline_step_types():
    plan = build_plan_from_decision(_make_decision(), "crée une scène Blender")
    step_types = [s.step_type for s in plan.steps]
    assert step_types == ["prepare_blender_script", "tool_blender"]


def test_blender_pipeline_step_ids():
    plan = build_plan_from_decision(_make_decision(), "crée une scène Blender")
    step_ids = [s.step_id for s in plan.steps]
    assert "step_prepare_blender" in step_ids
    assert "step_run_blender" in step_ids


def test_blender_pipeline_dependency():
    plan = build_plan_from_decision(_make_decision(), "crée une scène Blender")
    tool_step = next(s for s in plan.steps if s.step_type == "tool_blender")
    assert "step_prepare_blender" in tool_step.depends_on


def test_blender_pipeline_agent_and_model():
    plan = build_plan_from_decision(_make_decision(), "crée une scène Blender")
    prepare_step = next(s for s in plan.steps if s.step_type == "prepare_blender_script")
    assert prepare_step.agent == "AGENT_BUILDER_IA"
    assert prepare_step.model == "qwen2.5-coder:7b"


def test_blender_pipeline_tool_blender_has_tool():
    plan = build_plan_from_decision(_make_decision(), "crée une scène Blender")
    tool_step = next(s for s in plan.steps if s.step_type == "tool_blender")
    assert tool_step.tool == "blender"


def test_non_blender_tool_does_not_produce_blender_pipeline():
    """selected_tool comfyui ne doit pas déclencher blender_pipeline."""
    plan = build_plan_from_decision(
        _make_decision(task_type="image_generation", selected_tool="comfyui"),
        "génère une image",
    )
    assert plan.strategy != "blender_pipeline"
