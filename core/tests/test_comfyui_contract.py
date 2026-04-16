from app.clients.comfyui_client import VisualRequest, build_visual_request_from_text, run_comfyui_workflow
from app.engine.planner_types import ExecutionPlan, ExecutionState, PlanStep


def test_build_visual_request_from_text():
    request = build_visual_request_from_text("génère une image cyberpunk")
    assert isinstance(request, VisualRequest)
    assert request.workflow_id == "cinematic_scene_v1"
    assert request.positive_prompt == "génère une image cyberpunk"


def test_run_comfyui_workflow_accepts_string(monkeypatch):
    monkeypatch.setattr("app.clients.comfyui_client.ensure_comfyui_ready", lambda timeout_seconds=20: None)
    monkeypatch.setattr("app.clients.comfyui_client.queue_prompt", lambda workflow: "prompt_123")
    monkeypatch.setattr("app.clients.comfyui_client.wait_for_completion", lambda prompt_id, timeout_seconds=120: {"outputs": {}})
    monkeypatch.setattr("app.clients.comfyui_client.extract_output_path", lambda history: None)

    result = run_comfyui_workflow("génère une image cyberpunk")
    assert result["prompt_id"] == "prompt_123"


def test_prepare_visual_step_stores_visual_request(monkeypatch):
    from app.engine.step_executor import execute_step

    state = ExecutionState(
        message="génère une image cyberpunk",
        decision={},
        plan=ExecutionPlan(task_type="image_generation", steps=[]),
    )
    step = PlanStep(step_id="step_prepare_visual", step_type="prepare_visual", goal="prep")
    result = execute_step(state, step)

    assert result.status == "success"
    assert "visual_request" in state.context
    assert state.context["visual_request"].workflow_id == "cinematic_scene_v1"



def test_prepare_visual_step_exposes_workflow_reason():
    from app.engine.step_executor import execute_step

    state = ExecutionState(
        message="portrait cinématique d'un personnage sombre",
        decision={},
        plan=ExecutionPlan(task_type="image_generation", steps=[]),
    )
    step = PlanStep(step_id="step_prepare_visual", step_type="prepare_visual", goal="prep")
    result = execute_step(state, step)

    assert result.status == "success"
    assert result.meta["workflow_id"] == "portrait_basic_v1"
    assert "portrait" in result.meta["workflow_reason"].lower()
