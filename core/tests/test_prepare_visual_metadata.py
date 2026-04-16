from __future__ import annotations

from app.engine.executor import execute_request


def test_prepare_visual_exposes_visual_analysis_metadata(monkeypatch):
    def fake_run_comfyui_workflow(request):
        return {
            "status": "success",
            "workflow_id": request.workflow_id,
            "filename": "out.png",
            "output_path": "outputs/out.png",
            "filenames": ["out.png"],
            "output_paths": ["outputs/out.png"],
            "parameters": request.to_dict(),
            "prompt_id": "prompt_123",
            "raw_response": {"ok": True},
            "variants_count": request.variants_count,
            "completed_variants": request.variants_count,
            "partial": False,
        }

    monkeypatch.setattr("app.engine.step_executor.run_comfyui_workflow", fake_run_comfyui_workflow)

    result = execute_request("packshot de parfum luxe", False)

    prepare_step = next(step for step in result["step_results"] if step["step_type"] == "prepare_visual")
    meta = prepare_step["meta"]

    assert meta["workflow_id"] == "object_basic_v1"
    assert meta["subject_type"] == "product"
    assert meta["render_intent"] == "packshot"
    assert "luxury" in meta["style_flags"]
    assert "workflow=object_basic_v1" in meta["workflow_reason"]
    assert meta["parameters"]["width"] == 1024
    assert meta["parameters"]["height"] == 1024