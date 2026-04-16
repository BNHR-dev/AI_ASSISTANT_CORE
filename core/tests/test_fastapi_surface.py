from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_health_endpoint():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_v1_models_endpoint_registered():
    response = client.get("/v1/models")

    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "list"
    assert [model["id"] for model in body["data"]] == [
        "assistant-core-auto",
        "assistant-core-prof",
        "assistant-core-builder",
        "assistant-core-archi",
        "assistant-core-exam",
    ]


def test_execute_endpoint_exposes_execution_summary(monkeypatch):
    monkeypatch.setattr(
        "app.main.execute_request",
        lambda message, has_image: {
            "task_type": "build",
            "primary_agent": "AGENT_BUILDER_IA",
            "selected_model": "qwen2.5-coder:14b",
            "needs_web": False,
            "second_call": None,
            "output_format": "code",
            "selected_tool": None,
            "matched_rule": "build_mode",
            "reason_debug": "build test",
            "classifier_reason": "build test",
            "decision_trace": ["classifier → build"],
            "decision_path": ["classifier", "build"],
            "reason": "build test",
            "execution_strategy": "single_step",
            "execution_summary": {
                "status": "success",
                "total_steps": 1,
                "successful_step_ids": ["step_primary"],
                "error_step_ids": [],
                "blocked_step_ids": [],
            },
            "plan": [],
            "step_results": [],
            "primary_output": "OK",
            "second_output": None,
            "output": "OK",
        },
    )

    response = client.post("/execute", json={"message": "écris un script", "has_image": False})

    assert response.status_code == 200
    body = response.json()
    assert body["execution_summary"]["status"] == "success"
    assert body["execution_summary"]["successful_step_ids"] == ["step_primary"]



def test_execute_endpoint_exposes_observability_fields(monkeypatch):
    monkeypatch.setattr(
        "app.main.execute_request",
        lambda message, has_image: {
            "task_type": "build",
            "primary_agent": "AGENT_BUILDER_IA",
            "selected_model": "qwen2.5-coder:14b",
            "needs_web": False,
            "second_call": None,
            "output_format": "code",
            "selected_tool": None,
            "matched_rule": "build_mode",
            "reason_debug": "build test",
            "classifier_reason": "build test",
            "decision_trace": ["classifier → build"],
            "decision_path": ["classifier", "build"],
            "reason": "build test",
            "execution_strategy": "single_step",
            "execution_summary": {
                "status": "success",
                "total_steps": 1,
                "successful_step_ids": ["step_primary"],
                "error_step_ids": [],
                "blocked_step_ids": [],
            },
            "request_id": "req_abc",
            "started_at": "2026-04-06T10:00:00+00:00",
            "finished_at": "2026-04-06T10:00:00.500000+00:00",
            "duration_ms": 500,
            "plan": [],
            "step_results": [],
            "primary_output": "OK",
            "second_output": None,
            "output": "OK",
        },
    )

    response = client.post("/execute", json={"message": "écris un script", "has_image": False})

    assert response.status_code == 200
    body = response.json()
    assert body["request_id"] == "req_abc"
    assert body["duration_ms"] == 500
