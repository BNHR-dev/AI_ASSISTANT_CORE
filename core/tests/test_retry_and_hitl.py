"""
Tests du retry déclaratif par step et du human-in-the-loop (chantier 4B).

Invariants couverts :
- Retry : max_attempts=1 par défaut = comportement historique (un seul
  appel) ; un step qui échoue puis réussit dans la borne finit success
  avec UN seul StepResult (meta.attempts) et des événements step.retry ;
  borne épuisée → error, nombre d'appels exact.
- plan_builder : AAC_TOOL_RETRY_MAX_ATTEMPTS ne touche QUE les steps
  outils, borné à [1, 5], valeur invalide → 1.
- HITL : pause_before_tools marque les steps outils ; le run s'arrête
  AVANT le step outil (jamais exécuté), statut "paused", awaiting_step_ids
  renseigné, checkpoint sur disque ; resume vaut approbation (le step
  s'exécute, plus de re-pause) ; l'événement step.awaiting_user est
  journalisé ; les steps amont (prepare) restaurés sans ré-exécution.
- Console : la vue marque is_paused ; POST /console/resume reprend et
  rend le fragment résultat.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.engine import run_state as rstate
from app.engine.executor import execute_request, resume_request
from app.engine.plan_builder import build_plan_from_decision
from app.engine.visual_types import VisualRequest

_VISUAL_REQUEST = VisualRequest(workflow_id="object_basic_v1", positive_prompt="test")

_VISUAL_DECISION = {
    "task_type": "image_generation",
    "primary_agent": "AGENT_CREATIVE_IA",
    "selected_model": "qwen3:8b",
    "selected_tool": "comfyui",
    "output_format": "image",
    "needs_web": False,
    "second_call": None,
    "matched_rule": None,
    "reason": "test",
    "reason_debug": "test",
    "classifier_reason": "test",
    "decision_trace": ["classifier → image_generation"],
    "decision_path": ["classifier", "image_generation"],
}


@pytest.fixture
def hitl_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("AAC_RUN_STATE_ENABLED", "1")
    monkeypatch.setenv("AAC_RUN_EVENTS_ENABLED", "1")
    monkeypatch.setenv("AAC_RUN_EVENTS_DIR", str(tmp_path))
    return tmp_path


def _patch_visual_decision(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.engine.executor.build_route_decision",
        lambda message, has_image: dict(_VISUAL_DECISION),
    )


def _events(base: Path, request_id: str) -> list[dict]:
    path = base / request_id / "events.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# plan_builder — AAC_TOOL_RETRY_MAX_ATTEMPTS
# ---------------------------------------------------------------------------

def _step_attempts(decision: dict) -> dict[str, int]:
    plan = build_plan_from_decision(decision, "x")
    return {step.step_id: step.max_attempts for step in plan.steps}


def test_default_no_retry_anywhere(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AAC_TOOL_RETRY_MAX_ATTEMPTS", raising=False)
    attempts = _step_attempts(dict(_VISUAL_DECISION))
    assert attempts == {"step_prepare_visual": 1, "step_run_comfyui": 1}


def test_env_sets_tool_steps_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AAC_TOOL_RETRY_MAX_ATTEMPTS", "3")
    attempts = _step_attempts(dict(_VISUAL_DECISION))
    assert attempts["step_run_comfyui"] == 3
    assert attempts["step_prepare_visual"] == 1  # jamais les steps LLM


@pytest.mark.parametrize("raw,expected", [("0", 1), ("99", 5), ("abc", 1)])
def test_env_bounds_and_garbage(monkeypatch, raw: str, expected: int) -> None:
    monkeypatch.setenv("AAC_TOOL_RETRY_MAX_ATTEMPTS", raw)
    assert _step_attempts(dict(_VISUAL_DECISION))["step_run_comfyui"] == expected


# ---------------------------------------------------------------------------
# Retry à l'exécution
# ---------------------------------------------------------------------------

def _run_visual_with_tool(monkeypatch, tool_behavior, max_attempts: int) -> dict:
    """Exécute le pipeline visuel avec un outil ComfyUI substitué."""
    _patch_visual_decision(monkeypatch)
    monkeypatch.setenv("AAC_TOOL_RETRY_MAX_ATTEMPTS", str(max_attempts))
    monkeypatch.setattr(
        "app.engine.step_executor.build_visual_request_from_text",
        lambda text: _VISUAL_REQUEST,
    )
    monkeypatch.setattr(
        "app.engine.step_executor.analyze_visual_intent",
        lambda text: type("A", (), {"reason": "t", "subject_type": "o",
                                    "render_intent": "r", "style_flags": [],
                                    "subject_scores": {}, "render_scores": {},
                                    "to_dict": lambda self: {}})(),
    )
    monkeypatch.setattr("app.engine.step_executor.run_comfyui_workflow", tool_behavior)
    return execute_request("génère une image de test")


def test_step_succeeds_within_retry_budget(hitl_env: Path, monkeypatch) -> None:
    calls = {"n": 0}

    def flaky(request):
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError(f"transient {calls['n']}")
        return {"status": "success", "output_path": "/tmp/x.png", "filename": "x.png",
                "parameters": {}}

    result = _run_visual_with_tool(monkeypatch, flaky, max_attempts=3)

    assert result["execution_summary"]["status"] == "success"
    assert calls["n"] == 3
    # UN seul StepResult pour le step outil, annoté du nombre de tentatives.
    tool_results = [r for r in result["step_results"] if r["step_id"] == "step_run_comfyui"]
    assert len(tool_results) == 1
    assert tool_results[0]["meta"].get("attempts") == 3
    retries = [e for e in _events(hitl_env, result["request_id"]) if e["kind"] == "step.retry"]
    assert [e["data"]["attempt"] for e in retries] == [1, 2]


def test_retry_budget_exhausted_is_error(hitl_env: Path, monkeypatch) -> None:
    calls = {"n": 0}

    def always_down(request):
        calls["n"] += 1
        raise RuntimeError("down")

    result = _run_visual_with_tool(monkeypatch, always_down, max_attempts=3)

    assert result["execution_summary"]["status"] == "degraded"
    assert calls["n"] == 3  # exactement la borne, pas une de plus
    assert result["execution_summary"]["error_step_ids"] == ["step_run_comfyui"]


def test_no_retry_by_default_single_call(hitl_env: Path, monkeypatch) -> None:
    calls = {"n": 0}

    def always_down(request):
        calls["n"] += 1
        raise RuntimeError("down")

    result = _run_visual_with_tool(monkeypatch, always_down, max_attempts=1)
    assert calls["n"] == 1  # comportement historique intact
    assert result["execution_summary"]["status"] == "degraded"


# ---------------------------------------------------------------------------
# HITL — pause avant outil, reprise = approbation
# ---------------------------------------------------------------------------

def test_pause_stops_before_tool_and_resume_approves(hitl_env: Path, monkeypatch) -> None:
    _patch_visual_decision(monkeypatch)
    tool_calls = {"n": 0}

    def tool(request):
        tool_calls["n"] += 1
        return {"status": "success", "output_path": "/tmp/x.png", "filename": "x.png",
                "parameters": {}}

    monkeypatch.setattr("app.engine.step_executor.build_visual_request_from_text", lambda t: _VISUAL_REQUEST)
    monkeypatch.setattr(
        "app.engine.step_executor.analyze_visual_intent",
        lambda text: type("A", (), {"reason": "t", "subject_type": "o",
                                    "render_intent": "r", "style_flags": [],
                                    "subject_scores": {}, "render_scores": {},
                                    "to_dict": lambda self: {}})(),
    )
    monkeypatch.setattr("app.engine.step_executor.run_comfyui_workflow", tool)

    paused = execute_request("génère une image de test", pause_before_tools=True)

    # Le run est en pause AVANT l'outil : jamais exécuté.
    assert paused["execution_summary"]["status"] == "paused"
    assert paused["execution_summary"]["awaiting_step_ids"] == ["step_run_comfyui"]
    assert paused["execution_summary"]["successful_step_ids"] == ["step_prepare_visual"]
    assert tool_calls["n"] == 0
    # Checkpoint en pause sur disque + événement journalisé.
    saved = rstate.load_run_state(paused["request_id"])
    assert saved["run_status"] == "paused"
    kinds = [e["kind"] for e in _events(hitl_env, paused["request_id"])]
    assert "step.awaiting_user" in kinds

    resumed = resume_request(paused["request_id"])

    assert resumed["request_id"] == paused["request_id"]
    assert resumed["execution_summary"]["status"] == "success"
    assert tool_calls["n"] == 1  # exécuté UNE fois, après approbation
    # Le step amont a été restauré, pas ré-exécuté (un seul prepare au total).
    prepare = [r for r in resumed["step_results"] if r["step_id"] == "step_prepare_visual"]
    assert len(prepare) == 1


def test_pause_flag_off_changes_nothing(hitl_env: Path, monkeypatch) -> None:
    _patch_visual_decision(monkeypatch)
    monkeypatch.setattr("app.engine.step_executor.build_visual_request_from_text", lambda t: _VISUAL_REQUEST)
    monkeypatch.setattr(
        "app.engine.step_executor.analyze_visual_intent",
        lambda text: type("A", (), {"reason": "t", "subject_type": "o",
                                    "render_intent": "r", "style_flags": [],
                                    "subject_scores": {}, "render_scores": {},
                                    "to_dict": lambda self: {}})(),
    )
    monkeypatch.setattr(
        "app.engine.step_executor.run_comfyui_workflow",
        lambda request: {"status": "success", "output_path": "/tmp/x.png",
                         "filename": "x.png", "parameters": {}},
    )
    result = execute_request("génère une image de test")
    assert result["execution_summary"]["status"] == "success"
    assert result["execution_summary"]["awaiting_step_ids"] == []


# ---------------------------------------------------------------------------
# Console — vue paused + reprise
# ---------------------------------------------------------------------------

def test_build_view_flags_paused() -> None:
    import console

    view = console.build_view(
        {
            "execution_summary": {"status": "paused", "total_steps": 2,
                                  "successful_step_ids": ["step_prepare_visual"],
                                  "error_step_ids": [], "blocked_step_ids": [],
                                  "awaiting_step_ids": ["step_run_comfyui"]},
            "step_results": [], "plan": [], "request_id": "req-1",
        }
    )
    assert view["is_paused"] is True
    assert view["has_error"] is False


def test_console_resume_route_renders_result(monkeypatch) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import console

    monkeypatch.setattr(
        "app.engine.executor.resume_request",
        lambda request_id: {
            "task_type": "image_generation", "selected_model": "m",
            "execution_summary": {"status": "success", "total_steps": 2,
                                  "successful_step_ids": ["a", "b"],
                                  "error_step_ids": [], "blocked_step_ids": [],
                                  "awaiting_step_ids": []},
            "step_results": [], "plan": [], "request_id": request_id,
            "duration_ms": 5, "output": "done", "decision_path": [],
        },
    )
    app = FastAPI()
    app.include_router(console.router)
    client = TestClient(app)

    response = client.post("/console/resume", params={"request_id": "req-9"})
    assert response.status_code == 200
    assert "success" in response.text


def test_console_resume_route_unknown_run(monkeypatch) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import console

    def missing(request_id):
        raise LookupError("no saved state")

    monkeypatch.setattr("app.engine.executor.resume_request", missing)
    app = FastAPI()
    app.include_router(console.router)
    client = TestClient(app)

    response = client.post("/console/resume", params={"request_id": "nope"})
    assert response.status_code == 200
    assert "No saved state" in response.text
