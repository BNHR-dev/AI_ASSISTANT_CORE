from app.engine.executor import execute_request


def _base_visual_decision():
    return {
        "task_type": "image_generation",
        "primary_agent": "AGENT_CREATIVE_IA",
        "selected_model": "qwen3:8b",
        "selected_tool": "comfyui",
        "output_format": "image",
        "needs_web": False,
        "second_call": None,
        "matched_rule": "image_mode",
        "reason": "image test",
        "reason_debug": "image test",
        "classifier_reason": "image test",
        "decision_trace": ["classifier → image_generation"],
        "decision_path": ["classifier", "image_generation"],
    }


def test_visual_pipeline_partial_success_is_exposed(monkeypatch):
    monkeypatch.setattr("app.engine.executor.build_route_decision", lambda message, has_image: _base_visual_decision())
    monkeypatch.setattr(
        "app.engine.step_executor.run_comfyui_workflow",
        lambda request: {
            "status": "success",
            "workflow_id": request.workflow_id,
            "filename": "out.png",
            "output_path": "outputs/out.png",
            "filenames": ["out.png", "out_v2.png", "out_v3.png"],
            "output_paths": ["outputs/out.png", "outputs/out_v2.png", "outputs/out_v3.png"],
            "parameters": request.to_dict(),
            "prompt_id": "prompt_123",
            "raw_response": {"ok": True},
            "variants_count": 4,
            "completed_variants": 3,
            "partial": True,
            "error": "variant 4/4 failed: timeout",
        },
    )

    result = execute_request("génère 4 variantes d'une image cyberpunk", False)

    assert result["execution_summary"]["status"] == "success"
    assert result["artifact_path"] == "outputs/out.png"
    assert result["completed_variants"] == 3
    assert result["variants_count"] == 4
    assert result["partial_visual_success"] is True
    assert "3 variantes générées sur 4." in result["output"]


def test_visual_pipeline_no_usable_output_surfaces_error(monkeypatch):
    monkeypatch.setattr("app.engine.executor.build_route_decision", lambda message, has_image: _base_visual_decision())
    monkeypatch.setattr(
        "app.engine.step_executor.run_comfyui_workflow",
        lambda request: {
            "status": "error",
            "workflow_id": request.workflow_id,
            "filename": None,
            "output_path": None,
            "filenames": [],
            "output_paths": [],
            "parameters": request.to_dict(),
            "prompt_id": None,
            "raw_response": {"ok": False},
            "variants_count": 2,
            "completed_variants": 0,
            "partial": False,
            "error": "ComfyUI completed, but no usable output files were detected.",
        },
    )

    result = execute_request("génère 2 variantes d'une image cyberpunk", False)

    assert result["execution_summary"]["status"] == "degraded"
    assert result["artifact_path"] is None
    assert result["completed_variants"] == 0
    assert result["variants_count"] == 2
    assert result["partial_visual_success"] is False
    assert result["step_results"][1]["status"] == "error"
    assert "no usable output files" in result["output"].lower()
