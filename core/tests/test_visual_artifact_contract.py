from app.main import execute
from app.schemas import ExecuteRequest


def test_execute_response_exposes_visual_artifact_fields(monkeypatch):
    monkeypatch.setattr(
        "app.main.execute_request",
        lambda message, has_image, **kwargs: {
            "task_type": "image_generation",
            "primary_agent": "AGENT_CREATIVE_IA",
            "selected_model": "qwen3:14b",
            "needs_web": False,
            "second_call": None,
            "output_format": "prompt visuel structuré",
            "selected_tool": "comfyui",
            "matched_rule": None,
            "reason_debug": "image",
            "classifier_reason": "image",
            "decision_trace": ["classifier → image_generation"],
            "decision_path": ["classifier → image_generation"],
            "reason": "image",
            "execution_strategy": "visual_pipeline",
            "execution_summary": {
                "status": "success",
                "total_steps": 2,
                "successful_step_ids": ["step_prepare_visual", "step_run_comfyui"],
                "error_step_ids": [],
                "blocked_step_ids": [],
            },
            "request_id": "req_img",
            "started_at": "2026-04-06T10:00:00+00:00",
            "finished_at": "2026-04-06T10:00:01+00:00",
            "duration_ms": 1000,
            "plan": [],
            "step_results": [],
            "primary_output": None,
            "second_output": None,
            "artifact_type": "image",
            "artifact_path": "outputs/out.png",
            "artifact_filename": "out.png",
            "artifact_paths": ["outputs/out.png", "outputs/out_v2.png"],
            "artifact_filenames": ["out.png", "out_v2.png"],
            "workflow_id": "cinematic_scene_v1",
            "comfyui_status": "success",
            "comfyui_prompt_id": "prompt_123",
            "variants_count": 2,
            "completed_variants": 2,
            "partial_visual_success": False,
            "output": "2 variantes générées avec succès sur 2.",
        },
    )

    response = execute(ExecuteRequest(message="génère une image", has_image=False))

    assert response.execution_strategy == "visual_pipeline"
    assert response.artifact_type == "image"
    assert response.artifact_path == "outputs/out.png"
    assert response.artifact_filename == "out.png"
    assert response.artifact_paths == ["outputs/out.png", "outputs/out_v2.png"]
    assert response.artifact_filenames == ["out.png", "out_v2.png"]
    assert response.workflow_id == "cinematic_scene_v1"
    assert response.comfyui_status == "success"
    assert response.comfyui_prompt_id == "prompt_123"
    assert response.variants_count == 2
    assert response.completed_variants == 2
    assert response.partial_visual_success is False
