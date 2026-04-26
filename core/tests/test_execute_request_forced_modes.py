from app.engine.executor import execute_request


def test_execute_request_forced_vision_mode_runs_single_step(monkeypatch):
    monkeypatch.setattr(
        "app.engine.step_executor.generate_with_ollama",
        lambda model, prompt: "VISION_OUTPUT",
    )

    result = execute_request("décris ce paysage nocturne", mode="vision")

    assert result["task_type"] == "vision"
    assert result["execution_strategy"] == "single_step"
    assert len(result["plan"]) == 1
    assert result["plan"][0]["step_type"] == "llm_primary"
    assert result["output"] == "VISION_OUTPUT"
    assert result["execution_summary"]["status"] == "success"


def test_execute_request_forced_critique_mode_runs_single_step(monkeypatch):
    monkeypatch.setattr(
        "app.engine.step_executor.generate_with_ollama",
        lambda model, prompt: "CRITIQUE_OUTPUT",
    )

    result = execute_request("critique ce code python", mode="critique")

    assert result["task_type"] == "critique"
    assert result["execution_strategy"] == "single_step"
    assert len(result["plan"]) == 1
    assert result["plan"][0]["step_type"] == "llm_primary"
    assert result["output"] == "CRITIQUE_OUTPUT"
    assert result["execution_summary"]["status"] == "success"


def test_execute_request_forced_web_research_mode_runs_web_pipeline(monkeypatch):
    monkeypatch.setattr(
        "app.engine.step_executor.search_web",
        lambda query: [
            {
                "title": "Deep Learning — introduction",
                "url": "https://example.com/deep-learning",
                "content": "Présentation du deep learning.",
                "source": "example.com",
                "published_at": None,
                "kind": "article",
                "news_like": False,
            }
        ],
    )
    monkeypatch.setattr(
        "app.engine.step_executor.generate_with_ollama",
        lambda model, prompt: "SYNTHÈSE WEB FORCÉE",
    )

    result = execute_request("qu'est-ce que le deep learning", mode="web_research")

    assert result["task_type"] == "web_research"
    assert result["execution_strategy"] == "web_pipeline"
    assert len(result["plan"]) == 2
    assert result["plan"][0]["step_type"] == "tool_web_search"
    assert result["plan"][1]["step_type"] == "llm_synthesis"
    assert result["output"] == "SYNTHÈSE WEB FORCÉE"
    assert result["execution_summary"]["status"] == "success"


def test_execute_request_forced_image_generation_mode_runs_visual_pipeline(monkeypatch):
    monkeypatch.setattr(
        "app.engine.step_executor.run_comfyui_workflow",
        lambda request: {
            "status": "success",
            "workflow_id": request.workflow_id,
            "filename": "nuit.png",
            "output_path": "outputs/nuit.png",
            "filenames": ["nuit.png"],
            "output_paths": ["outputs/nuit.png"],
            "parameters": request.to_dict(),
            "prompt_id": "prompt-forced-001",
            "raw_response": {"ok": True},
            "variants_count": 1,
            "completed_variants": 1,
            "partial": False,
        },
    )

    result = execute_request("génère une image sombre", mode="image_generation")

    assert result["task_type"] == "image_generation"
    assert result["selected_tool"] == "comfyui"
    assert result["execution_strategy"] == "visual_pipeline"
    assert len(result["plan"]) == 2
    assert result["plan"][0]["step_type"] == "prepare_visual"
    assert result["plan"][1]["step_type"] == "tool_comfyui"
    assert result["artifact_type"] == "image"
    assert result["artifact_path"] == "outputs/nuit.png"
    assert result["execution_summary"]["status"] == "success"
