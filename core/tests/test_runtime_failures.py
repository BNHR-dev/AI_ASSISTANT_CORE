from app.engine.executor import execute_request


def test_web_pipeline_surfaces_tool_fallback_and_blocks_synthesis(monkeypatch):
    monkeypatch.setattr(
        "app.engine.executor.build_route_decision",
        lambda message, has_image: {
            "task_type": "web_research",
            "primary_agent": "AGENT_PROF_IA",
            "selected_model": "qwen3:8b",
            "selected_tool": "web",
            "output_format": "synthèse claire",
            "needs_web": True,
            "second_call": None,
            "matched_rule": "web_mode",
            "reason": "web test",
            "reason_debug": "web test",
            "classifier_reason": "web test",
            "decision_trace": ["classifier → web_research"],
            "decision_path": ["classifier", "web_research"],
        },
    )
    monkeypatch.setattr("app.engine.step_executor.search_web", lambda message: (_ for _ in ()).throw(RuntimeError("connection refused")))

    result = execute_request("cherche des news")

    assert "La recherche web n'a pas pu aboutir." in result["output"]
    assert result["step_results"][0]["status"] == "error"
    assert result["step_results"][1]["status"] == "blocked"


def test_llm_step_surfaces_ollama_fallback(monkeypatch):
    monkeypatch.setattr(
        "app.engine.executor.build_route_decision",
        lambda message, has_image: {
            "task_type": "build",
            "primary_agent": "AGENT_BUILDER_IA",
            "selected_model": "qwen2.5-coder:14b",
            "selected_tool": None,
            "output_format": "code",
            "needs_web": False,
            "second_call": None,
            "matched_rule": "build_mode",
            "reason": "build test",
            "reason_debug": "build test",
            "classifier_reason": "build test",
            "decision_trace": ["classifier → build"],
            "decision_path": ["classifier", "build"],
        },
    )
    monkeypatch.setattr("app.engine.step_executor.generate_with_ollama", lambda model, prompt: (_ for _ in ()).throw(RuntimeError("connection refused")))

    result = execute_request("écris un script")

    assert "Le moteur LLM local n'a pas pu répondre." in result["output"]
    assert result["step_results"][0]["status"] == "error"


def test_visual_pipeline_surfaces_comfyui_fallback(monkeypatch):
    monkeypatch.setattr(
        "app.engine.executor.build_route_decision",
        lambda message, has_image: {
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
        },
    )
    monkeypatch.setattr("app.engine.step_executor.run_comfyui_workflow", lambda request: (_ for _ in ()).throw(RuntimeError("runtime unavailable")))

    result = execute_request("génère une image")

    assert "ComfyUI est inaccessible actuellement." in result["output"]
    assert result["step_results"][1]["status"] == "error"



def test_execute_request_adds_step_timing_metadata(monkeypatch):
    monkeypatch.setattr(
        "app.engine.executor.build_route_decision",
        lambda message, has_image: {
            "task_type": "build",
            "primary_agent": "AGENT_BUILDER_IA",
            "selected_model": "qwen2.5-coder:14b",
            "selected_tool": None,
            "output_format": "code",
            "needs_web": False,
            "second_call": None,
            "matched_rule": "build_mode",
            "reason": "build test",
            "reason_debug": "build test",
            "classifier_reason": "build test",
            "decision_trace": ["classifier → build"],
            "decision_path": ["classifier", "build"],
        },
    )
    monkeypatch.setattr("app.engine.step_executor.generate_with_ollama", lambda model, prompt: "OK")

    result = execute_request("écris un script")

    assert result["request_id"]
    assert result["duration_ms"] >= 0
    assert result["step_results"][0]["started_at"] is not None
    assert result["step_results"][0]["finished_at"] is not None
    assert result["step_results"][0]["duration_ms"] >= 0
