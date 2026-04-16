from __future__ import annotations

from app.engine.executor import execute_request


def test_execute_request_builds_visual_request_and_returns_metadata(monkeypatch):
    captured = {}

    def fake_run_comfyui_workflow(request):
        captured["request"] = request
        return {
            "status": "success",
            "workflow_id": request.workflow_id,
            "filename": "out.png",
            "output_path": "outputs/out.png",
            "filenames": ["out.png", "out_v2.png"],
            "output_paths": ["outputs/out.png", "outputs/out_v2.png"],
            "parameters": request.to_dict(),
            "prompt_id": "prompt_123",
            "raw_response": {"ok": True},
            "variants_count": 2,
            "completed_variants": 2,
            "partial": False,
        }

    monkeypatch.setattr("app.engine.step_executor.run_comfyui_workflow", fake_run_comfyui_workflow)

    result = execute_request("génère 2 variantes d'une image cyberpunk avec néon humide", False)

    assert result["task_type"] == "image_generation"
    assert result["selected_tool"] == "comfyui"
    assert result["artifact_type"] == "image"
    assert result["workflow_id"] == "cinematic_scene_v1"
    assert result["artifact_filename"] == "out.png"
    assert result["artifact_path"] == "outputs/out.png"
    assert result["artifact_filenames"] == ["out.png", "out_v2.png"]
    assert result["artifact_paths"] == ["outputs/out.png", "outputs/out_v2.png"]
    assert result["variants_count"] == 2
    assert result["completed_variants"] == 2
    assert result["partial_visual_success"] is False
    assert result["comfyui_status"] == "success"
    assert result["comfyui_prompt_id"] == "prompt_123"
    assert captured["request"].positive_prompt == "génère 2 variantes d'une image cyberpunk avec néon humide"
    assert captured["request"].variants_count == 2
