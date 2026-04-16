from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_execute_image_generation_visual_pipeline_isolated(monkeypatch):
    def fake_run_comfyui_workflow(visual_request):
        return {
            "output_path": "fake_output/cyberpunk.png",
            "filename": "cyberpunk.png",
            "output_paths": ["fake_output/cyberpunk.png", "fake_output/cyberpunk_v2.png"],
            "filenames": ["cyberpunk.png", "cyberpunk_v2.png"],
            "artifact_type": "image",
            "artifact_path": "fake_output/cyberpunk.png",
            "artifact_filename": "cyberpunk.png",
            "workflow_id": visual_request.workflow_id,
            "comfyui_status": "success",
            "comfyui_prompt_id": "test-prompt-123",
            "variants_count": 2,
            "completed_variants": 2,
            "partial": False,
        }

    monkeypatch.setattr("app.engine.step_executor.run_comfyui_workflow", fake_run_comfyui_workflow)

    response = client.post(
        "/execute",
        json={
            "message": "génère 2 variantes d'une image cyberpunk",
            "has_image": False,
        },
    )

    assert response.status_code == 200
    data = response.json()

    assert data["task_type"] == "image_generation"
    assert data["selected_tool"] == "comfyui"
    assert data["execution_strategy"] == "visual_pipeline"

    plan = data["plan"]
    assert len(plan) == 2
    assert plan[0]["step_type"] == "prepare_visual"
    assert plan[1]["step_type"] == "tool_comfyui"

    step_results = data["step_results"]
    assert len(step_results) == 2
    assert step_results[0]["status"] == "success"
    assert step_results[1]["status"] == "success"

    assert data["artifact_type"] == "image"
    assert data["artifact_path"] == "fake_output/cyberpunk.png"
    assert data["artifact_path"].endswith("cyberpunk.png")
    assert data["artifact_paths"] == ["fake_output/cyberpunk.png", "fake_output/cyberpunk_v2.png"]
    assert data["artifact_filenames"] == ["cyberpunk.png", "cyberpunk_v2.png"]
    assert data["variants_count"] == 2
    assert data["completed_variants"] == 2
    assert data["partial_visual_success"] is False

    assert data["workflow_id"] == "cinematic_scene_v1"
    assert data["comfyui_status"] == "success"
