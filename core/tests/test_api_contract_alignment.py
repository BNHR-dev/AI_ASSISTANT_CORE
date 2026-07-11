from app.engine.output_contracts import OUTPUT_CONTRACTS
from app.engine.task_routing import TASK_ROUTING
from app.main import execute, route_request
from app.schemas import ExecuteRequest, RouteRequest


def test_route_response_exposes_decision_trace_fields(monkeypatch):
    monkeypatch.setattr(
        "app.main.build_route_decision",
        lambda message, has_image, **kwargs: {
            "task_type": "build",
            "primary_agent": "AGENT_BUILDER_IA",
            "selected_model": "qwen2.5-coder:14b",
            "needs_web": False,
            "second_call": None,
            "output_format": "code",
            "selected_tool": None,
            "matched_rule": None,
            "reason_debug": "keyword build",
            "classifier_reason": "keyword build",
            "decision_trace": ["classifier → build", "final_tool → None"],
            "decision_path": ["classifier → build", "final_tool → None"],
            "reason": "keyword build | Agent : AGENT_BUILDER_IA",
        },
    )

    response = route_request(RouteRequest(message="écris un script python"))

    assert response.classifier_reason == "keyword build"
    assert response.reason_debug == "keyword build"
    assert response.decision_trace == ["classifier → build", "final_tool → None"]
    assert response.decision_path == ["classifier → build", "final_tool → None"]


def test_execute_response_exposes_plan_and_trace(monkeypatch):
    monkeypatch.setattr(
        "app.main.execute_request",
        lambda message, has_image, **kwargs: {
            "task_type": "explain_basic",
            "primary_agent": "AGENT_PROF_IA",
            "selected_model": "qwen3:8b",
            "needs_web": False,
            "second_call": "build",
            "output_format": "définition + exemple",
            "selected_tool": None,
            "matched_rule": "explain_plus_code",
            "reason_debug": "explain + code",
            "classifier_reason": "explain + code",
            "decision_trace": ["classifier → explain_basic", "planner → strategy=two_step_llm"],
            "decision_path": ["classifier → explain_basic", "rule → explain_plus_code"],
            "reason": "explain + code | Agent : AGENT_PROF_IA",
            "execution_strategy": "two_step_llm",
            "plan": [
                {
                    "step_id": "step_primary",
                    "step_type": "llm_primary",
                    "goal": "Traiter la tâche principale",
                    "agent": "AGENT_PROF_IA",
                    "model": "qwen3:8b",
                    "tool": None,
                    "depends_on": [],
                    "status": "success",
                },
                {
                    "step_id": "step_secondary",
                    "step_type": "llm_secondary",
                    "goal": "Compléter la réponse",
                    "agent": None,
                    "model": None,
                    "tool": None,
                    "depends_on": ["step_primary"],
                    "status": "success",
                },
            ],
            "step_results": [
                {
                    "step_id": "step_primary",
                    "step_type": "llm_primary",
                    "status": "success",
                    "output": "EXPLAIN_OUTPUT",
                    "error": None,
                    "meta": {},
                },
                {
                    "step_id": "step_secondary",
                    "step_type": "llm_secondary",
                    "status": "success",
                    "output": "CODE_OUTPUT",
                    "error": None,
                    "meta": {"requested_task_type": "build"},
                },
            ],
            "primary_output": "EXPLAIN_OUTPUT",
            "second_output": "CODE_OUTPUT",
            "output": "EXPLAIN_OUTPUT\n\n---\n\nCODE_OUTPUT",
        },
    )

    response = execute(ExecuteRequest(message="explique avec code"))

    assert response.execution_strategy == "two_step_llm"
    assert len(response.plan) == 2
    assert len(response.step_results) == 2
    assert response.classifier_reason == "explain + code"
    assert response.reason_debug == "explain + code"
    assert response.primary_output == "EXPLAIN_OUTPUT"
    assert response.second_output == "CODE_OUTPUT"
    assert response.output == "EXPLAIN_OUTPUT\n\n---\n\nCODE_OUTPUT"



def test_execute_response_exposes_runtime_observability_fields(monkeypatch):
    monkeypatch.setattr(
        "app.main.execute_request",
        lambda message, has_image, **kwargs: {
            "task_type": "build",
            "primary_agent": "AGENT_BUILDER_IA",
            "selected_model": "qwen2.5-coder:14b",
            "needs_web": False,
            "second_call": None,
            "output_format": "code",
            "selected_tool": None,
            "matched_rule": None,
            "reason_debug": "build",
            "classifier_reason": "build",
            "decision_trace": ["classifier → build"],
            "decision_path": ["classifier → build"],
            "reason": "build",
            "execution_strategy": "single_step",
            "execution_summary": {
                "status": "success",
                "total_steps": 1,
                "successful_step_ids": ["step_primary"],
                "error_step_ids": [],
                "blocked_step_ids": [],
            },
            "request_id": "req_123",
            "started_at": "2026-04-06T10:00:00+00:00",
            "finished_at": "2026-04-06T10:00:01+00:00",
            "duration_ms": 1000,
            "plan": [],
            "step_results": [
                {
                    "step_id": "step_primary",
                    "step_type": "llm_primary",
                    "status": "success",
                    "output": "OK",
                    "error": None,
                    "meta": {},
                    "started_at": "2026-04-06T10:00:00+00:00",
                    "finished_at": "2026-04-06T10:00:01+00:00",
                    "duration_ms": 1000,
                }
            ],
            "primary_output": "OK",
            "second_output": None,
            "output": "OK",
        },
    )

    response = execute(ExecuteRequest(message="écris un script"))

    assert response.request_id == "req_123"
    assert response.started_at == "2026-04-06T10:00:00+00:00"
    assert response.finished_at == "2026-04-06T10:00:01+00:00"
    assert response.duration_ms == 1000
    assert response.step_results[0].duration_ms == 1000


def test_all_routed_task_types_have_explicit_output_contract():
    for task_type in TASK_ROUTING:
        assert task_type in OUTPUT_CONTRACTS, f"Missing contract for '{task_type}'"
