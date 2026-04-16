from app.engine.executor import execute_request


def test_execute_request_multistep(monkeypatch):
    def fake_generate(model: str, prompt: str) -> str:
        if "Réponse du premier appel" in prompt:
            return "CODE_OUTPUT"
        return "EXPLAIN_OUTPUT"

    monkeypatch.setattr("app.engine.step_executor.generate_with_ollama", fake_generate)

    from app.engine import executor as executor_module

    monkeypatch.setattr(
        executor_module,
        "build_route_decision",
        lambda message, has_image: {
            "task_type": "explain_basic",
            "primary_agent": "AGENT_PROF_IA",
            "selected_model": "qwen3:8b",
            "selected_tool": None,
            "output_format": "définition + image mentale + exemple concret",
            "needs_web": False,
            "second_call": "build",
            "decision_trace": ["classifier → explain_basic", "rule → explain_plus_code"],
        },
    )

    result = execute_request("Explique les embeddings avec code")
    assert result["primary_output"] == "EXPLAIN_OUTPUT"
    assert result["second_output"] == "CODE_OUTPUT"
    assert "EXPLAIN_OUTPUT" in result["output"]
    assert "CODE_OUTPUT" in result["output"]
    assert len(result["plan"]) == 2
