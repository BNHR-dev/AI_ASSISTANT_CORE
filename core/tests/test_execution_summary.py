from app.engine.executor import execute_request


def test_execution_summary_success_single_step(monkeypatch):
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

    assert result["execution_summary"]["status"] == "success"
    assert result["execution_summary"]["total_steps"] == 1
    assert result["execution_summary"]["successful_step_ids"] == ["step_primary"]
    assert result["execution_summary"]["error_step_ids"] == []
    assert result["execution_summary"]["blocked_step_ids"] == []


def test_execution_summary_degraded_when_secondary_fails(monkeypatch):
    monkeypatch.setattr(
        "app.engine.executor.build_route_decision",
        lambda message, has_image: {
            "task_type": "explain_basic",
            "primary_agent": "AGENT_PROF_IA",
            "selected_model": "qwen3:8b",
            "selected_tool": None,
            "output_format": "explication claire",
            "needs_web": False,
            "second_call": "build",
            "matched_rule": "explain_plus_code",
            "reason": "explain+build test",
            "reason_debug": "code demandé",
            "classifier_reason": "explain test",
            "decision_trace": ["classifier → explain_basic"],
            "decision_path": ["classifier", "explain_basic"],
        },
    )

    calls = iter(["PRIMARY_OK", RuntimeError("secondary down")])

    def fake_generate(model, prompt):
        value = next(calls)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr("app.engine.step_executor.generate_with_ollama", fake_generate)

    result = execute_request("explique puis code")

    assert result["execution_summary"]["status"] == "degraded"
    assert result["execution_summary"]["successful_step_ids"] == ["step_primary"]
    assert result["execution_summary"]["error_step_ids"] == ["step_secondary"]
    assert result["output"].startswith("PRIMARY_OK") or "Le moteur LLM local" in result["output"]
