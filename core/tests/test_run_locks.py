"""
Tests du verrou d'exécution par run (app.engine.run_locks).

Deux reprises simultanées du même run (double POST /resume, double clic
Console) doublaient les steps et écrivaient en même temps events.jsonl et
state.json. Invariants couverts :

- verrou : acquisition exclusive par request_id, RunBusyError immédiate,
  libération garantie même sur exception, deux ids distincts coexistent ;
- executor : pendant qu'un resume tourne (thread réel bloqué sur un Event),
  un second resume ET un execute du même id sont rejetés sans exécuter le
  moindre step ; le run gagnant se termine normalement et libère le verrou ;
- API : POST /resume sur un run actif → 409 (jamais de double exécution) ;
- Console : POST /console/resume sur un run actif ne planifie RIEN et
  ré-abonne le client au flux (idempotence du double clic) ; une
  RunBusyError perdue en arrière-plan n'écrase pas le registre de
  résultats.

LIMITE ASSUMÉE (documentée dans run_locks) : protection par-process
uniquement — le multi-worker reste hors périmètre.
"""
from __future__ import annotations

import threading
from pathlib import Path

import pytest

from app.engine import run_state as rstate
from app.engine.executor import execute_request, resume_request
from app.engine.planner_types import ExecutionPlan, PlanStep
from app.engine.run_locks import (
    RunBusyError,
    is_run_active,
    run_execution_lock,
)

_DECISION = {
    "task_type": "build",
    "primary_agent": "AGENT_BUILDER_IA",
    "selected_model": "qwen2.5-coder:14b",
    "selected_tool": None,
    "output_format": "code",
    "needs_web": False,
    "second_call": None,
    "matched_rule": None,
    "reason": "test",
    "reason_debug": "test",
    "classifier_reason": "test",
    "decision_trace": ["classifier → build"],
    "decision_path": ["classifier", "build"],
}


@pytest.fixture
def state_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("AAC_RUN_STATE_ENABLED", "1")
    monkeypatch.setenv("AAC_RUN_EVENTS_ENABLED", "1")
    monkeypatch.setenv("AAC_RUN_EVENTS_DIR", str(tmp_path))
    return tmp_path


def _save_single_step_checkpoint(request_id: str) -> None:
    plan = ExecutionPlan(
        task_type="build",
        strategy="single_step",
        steps=[PlanStep(step_id="step_a", step_type="llm_primary", goal="a",
                        agent="AGENT_BUILDER_IA", model="m")],
    )
    rstate.save_run_state(
        request_id, message="verrou", has_image=False, mode="auto",
        decision=dict(_DECISION), plan=plan, step_results=[],
    )


# ---------------------------------------------------------------------------
# Verrou nu
# ---------------------------------------------------------------------------

def test_lock_is_exclusive_per_id_and_released() -> None:
    assert is_run_active("req-l1") is False
    with run_execution_lock("req-l1"):
        assert is_run_active("req-l1") is True
        with pytest.raises(RunBusyError):
            with run_execution_lock("req-l1"):
                pass
        # Deux ids distincts coexistent.
        with run_execution_lock("req-l2"):
            assert is_run_active("req-l2") is True
    assert is_run_active("req-l1") is False
    with run_execution_lock("req-l1"):  # ré-acquérable après libération
        pass


def test_lock_released_on_exception() -> None:
    with pytest.raises(RuntimeError, match="boom"):
        with run_execution_lock("req-l3"):
            raise RuntimeError("boom")
    assert is_run_active("req-l3") is False


# ---------------------------------------------------------------------------
# Executor : concurrence réelle (thread bloqué sur un Event)
# ---------------------------------------------------------------------------

def test_concurrent_resume_and_execute_rejected(
    state_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    step_entered = threading.Event()
    release_step = threading.Event()
    calls: list[str] = []

    def blocking_llm(model, prompt):
        calls.append(model)
        step_entered.set()
        release_step.wait(timeout=10)
        return "OK"

    monkeypatch.setattr("app.engine.step_executor.generate_with_ollama", blocking_llm)
    _save_single_step_checkpoint("req-lock")

    results: dict = {}
    winner = threading.Thread(
        target=lambda: results.update(first=resume_request("req-lock"))
    )
    winner.start()
    try:
        assert step_entered.wait(timeout=10)  # le run gagnant tient le verrou

        with pytest.raises(RunBusyError):
            resume_request("req-lock")
        with pytest.raises(RunBusyError):
            execute_request("autre message", request_id="req-lock")
    finally:
        release_step.set()
        winner.join(timeout=10)

    assert not winner.is_alive()
    assert results["first"]["execution_summary"]["status"] == "success"
    assert calls == ["m"]  # le step n'a couru qu'UNE fois
    assert is_run_active("req-lock") is False  # verrou rendu en fin de run


# ---------------------------------------------------------------------------
# API : 409 sur run actif
# ---------------------------------------------------------------------------

def test_api_resume_busy_run_is_409(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    from app.main import create_app

    def busy(request_id):
        raise RunBusyError(request_id)

    monkeypatch.setattr("app.main.resume_request", busy)
    client = TestClient(create_app())
    response = client.post("/resume", json={"request_id": "req-busy"})
    assert response.status_code == 409
    assert "already executing" in response.json()["detail"]


# ---------------------------------------------------------------------------
# Console : double clic idempotent
# ---------------------------------------------------------------------------

def test_console_resume_active_run_reattaches_without_new_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import console

    resume_calls: list[str] = []
    monkeypatch.setattr(
        "app.engine.executor.resume_request",
        lambda request_id: resume_calls.append(request_id) or {},
    )
    app = FastAPI()
    app.include_router(console.router)
    client = TestClient(app)

    with run_execution_lock("req-active"):
        response = client.post("/console/resume", params={"request_id": "req-active"})

    assert response.status_code == 200
    assert "/console/stream/req-active?tail=1" in response.text
    assert resume_calls == []  # aucune reprise relancée


def test_console_background_drops_lost_race(monkeypatch: pytest.MonkeyPatch) -> None:
    """Une RunBusyError levée en arrière-plan (course perdue après le check
    du handler) ne doit PAS atterrir dans le registre : le run gagnant
    publiera son résultat, le flux du client continue."""
    import console

    def lost_race():
        raise RunBusyError("req-race")

    console._RESULTS.pop("req-race", None)
    console._run_in_background("req-race", lost_race)
    assert "req-race" not in console._RESULTS
