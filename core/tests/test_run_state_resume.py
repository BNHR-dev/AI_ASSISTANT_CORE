"""
Tests du checkpoint de run (app.engine.run_state) et de la reprise
(executor.resume_request) — chantier 4A.

Invariants couverts :
- save/load : round-trip fidèle, activation par env, écriture non-bloquante
  (IO impossible → pas d'exception), lecture tolérante (corrompu → None),
  champs inconnus d'une version future ignorés au rebuild.
- execute_request checkpointe après CHAQUE step (un crash au step 2 laisse
  le step 1 sur disque) puis stampe run_status au final.
- resume_request : restaure les steps RÉUSSIS sans les ré-exécuter (leurs
  sorties redeviennent disponibles pour les dépendants), ré-exécute les
  steps error/blocked/pending, émet run.resumed, re-checkpointe ;
  LookupError si aucun checkpoint.
- API POST /resume : 404 sur checkpoint absent, réponse ExecuteResponse.
- CLI aac resume : happy path + code de sortie 1 sur run non repris.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.engine import run_state as rstate
from app.engine.executor import execute_request, resume_request
from app.engine.planner_types import ExecutionPlan, PlanStep, StepResult

_DECISION = {
    "task_type": "explain_basic",
    "primary_agent": "AGENT_PROF_IA",
    "selected_model": "qwen3:8b",
    "selected_tool": None,
    "output_format": "explication claire",
    "needs_web": False,
    "second_call": "build",
    "matched_rule": "explain_plus_code",
    "reason": "test",
    "reason_debug": "test",
    "classifier_reason": "test",
    "decision_trace": ["classifier → explain_basic"],
    "decision_path": ["classifier", "explain_basic"],
}


@pytest.fixture
def state_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Active checkpoint + events vers un répertoire de test."""
    monkeypatch.setenv("AAC_RUN_STATE_ENABLED", "1")
    monkeypatch.setenv("AAC_RUN_EVENTS_ENABLED", "1")
    monkeypatch.setenv("AAC_RUN_EVENTS_DIR", str(tmp_path))
    return tmp_path


def _patch_decision(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.engine.executor.build_route_decision",
        lambda message, has_image: dict(_DECISION),
    )


# ---------------------------------------------------------------------------
# save / load / rebuild
# ---------------------------------------------------------------------------

def _plan() -> ExecutionPlan:
    return ExecutionPlan(
        task_type="explain_basic",
        strategy="two_steps",
        steps=[
            PlanStep(step_id="step_primary", step_type="agent", goal="expliquer"),
            PlanStep(step_id="step_secondary", step_type="agent", goal="coder",
                     depends_on=["step_primary"]),
        ],
    )


def test_save_and_load_round_trip(state_env: Path) -> None:
    plan = _plan()
    plan.steps[0].status = "success"
    results = [StepResult(step_id="step_primary", step_type="agent",
                          status="success", output="OK", duration_ms=12)]

    rstate.save_run_state(
        "req-1", message="explique x", has_image=False, mode="auto",
        decision=dict(_DECISION), plan=plan, step_results=results,
    )
    saved = rstate.load_run_state("req-1")

    assert saved is not None
    assert saved["message"] == "explique x"
    assert saved["run_status"] is None
    rebuilt_plan = rstate.rebuild_plan(saved["plan"])
    assert [s.step_id for s in rebuilt_plan.steps] == ["step_primary", "step_secondary"]
    assert rebuilt_plan.steps[0].status == "success"
    assert rebuilt_plan.steps[1].depends_on == ["step_primary"]
    rebuilt = rstate.rebuild_step_result(saved["step_results"][0])
    assert rebuilt.output == "OK" and rebuilt.duration_ms == 12


def test_save_disabled_writes_nothing(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AAC_RUN_STATE_ENABLED", "0")
    monkeypatch.setenv("AAC_RUN_EVENTS_DIR", str(tmp_path))
    rstate.save_run_state(
        "req-1", message="x", has_image=False, mode="auto",
        decision={}, plan=_plan(), step_results=[],
    )
    assert not (tmp_path / "req-1" / rstate.STATE_FILENAME).exists()


def test_save_never_raises_on_io_failure(monkeypatch, tmp_path: Path) -> None:
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setenv("AAC_RUN_STATE_ENABLED", "1")
    monkeypatch.setenv("AAC_RUN_EVENTS_DIR", str(blocker))
    rstate.save_run_state(  # ne lève pas
        "req-1", message="x", has_image=False, mode="auto",
        decision={}, plan=_plan(), step_results=[],
    )


@pytest.mark.parametrize("content", ["NOT JSON", "[1,2]", '{"plan": "nope", "message": "x"}', '{"plan": {}}'])
def test_load_tolerates_garbage(state_env: Path, content: str) -> None:
    run_dir = state_env / "req-bad"
    run_dir.mkdir()
    (run_dir / rstate.STATE_FILENAME).write_text(content, encoding="utf-8")
    assert rstate.load_run_state("req-bad") is None


def test_load_missing_returns_none(state_env: Path) -> None:
    assert rstate.load_run_state("no-such-run") is None


def test_rebuild_ignores_unknown_fields() -> None:
    # Un state.json écrit par une version FUTURE (champs en plus) se recharge.
    step = rstate.rebuild_step_result(
        {"step_id": "s1", "step_type": "agent", "status": "success",
         "field_from_the_future": 42}
    )
    assert step.step_id == "s1"
    plan = rstate.rebuild_plan(
        {"task_type": "build", "strategy": "single_step",
         "steps": [{"step_id": "s1", "step_type": "agent", "goal": "g", "novel": True}]}
    )
    assert plan.steps[0].goal == "g"


# ---------------------------------------------------------------------------
# execute_request : checkpoint par step + statut final
# ---------------------------------------------------------------------------

def test_execute_checkpoints_after_each_step(state_env: Path, monkeypatch) -> None:
    _patch_decision(monkeypatch)
    snapshots: list[int] = []

    def generate(model, prompt):
        # Photographie le checkpoint AU MOMENT du step suivant : le step 1
        # doit déjà être sur disque quand le step 2 s'exécute.
        saved = rstate.load_run_state(_current_request_id(state_env))
        snapshots.append(len((saved or {}).get("step_results") or []))
        return "OK"

    monkeypatch.setattr("app.engine.step_executor.generate_with_ollama", generate)
    result = execute_request("explique puis code")

    assert result["execution_summary"]["status"] == "success"
    # Au step 1 : aucun checkpoint encore ; au step 2 : le step 1 y est.
    assert snapshots == [0, 1]
    saved = rstate.load_run_state(result["request_id"])
    assert saved["run_status"] == "success"
    assert len(saved["step_results"]) == 2


def _current_request_id(base: Path) -> str:
    runs = [d.name for d in base.iterdir() if d.is_dir()]
    assert len(runs) == 1
    return runs[0]


def test_failed_run_leaves_resumable_state(state_env: Path, monkeypatch) -> None:
    _patch_decision(monkeypatch)
    calls = iter(["PRIMARY_OK", RuntimeError("ollama down")])

    def generate(model, prompt):
        value = next(calls)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr("app.engine.step_executor.generate_with_ollama", generate)
    result = execute_request("explique puis code")

    assert result["execution_summary"]["status"] == "degraded"
    saved = rstate.load_run_state(result["request_id"])
    assert saved["run_status"] == "degraded"
    statuses = {r["step_id"]: r["status"] for r in saved["step_results"]}
    assert statuses == {"step_primary": "success", "step_secondary": "error"}


# ---------------------------------------------------------------------------
# resume_request
# ---------------------------------------------------------------------------

def test_resume_reruns_only_failed_steps(state_env: Path, monkeypatch) -> None:
    _patch_decision(monkeypatch)
    first = iter(["PRIMARY_OK", RuntimeError("down")])
    monkeypatch.setattr(
        "app.engine.step_executor.generate_with_ollama",
        lambda model, prompt: (_ for _ in ()).throw(v) if isinstance(v := next(first), Exception) else v,
    )
    failed = execute_request("explique puis code")
    request_id = failed["request_id"]

    executed_prompts: list[str] = []

    def generate_ok(model, prompt):
        executed_prompts.append(prompt)
        return "SECONDARY_OK"

    monkeypatch.setattr("app.engine.step_executor.generate_with_ollama", generate_ok)
    resumed = resume_request(request_id)

    assert resumed["request_id"] == request_id
    assert resumed["execution_summary"]["status"] == "success"
    assert len(executed_prompts) == 1  # SEUL le step en échec a été rejoué
    # Le résultat du step restauré est intact, le step repris a sa sortie.
    assert resumed["primary_output"] == "PRIMARY_OK"
    assert resumed["second_output"] == "SECONDARY_OK"
    # Le checkpoint est re-stampé success.
    assert rstate.load_run_state(request_id)["run_status"] == "success"
    # L'événement run.resumed est journalisé avec le détail restauré/repris.
    events = [
        json.loads(line)
        for line in (state_env / request_id / "events.jsonl").read_text().splitlines()
    ]
    resumed_events = [e for e in events if e["kind"] == "run.resumed"]
    assert len(resumed_events) == 1
    assert resumed_events[0]["data"]["restored_step_ids"] == ["step_primary"]
    assert resumed_events[0]["data"]["pending_step_ids"] == ["step_secondary"]


def test_resume_unblocks_dependent_steps(state_env: Path, monkeypatch) -> None:
    # step_secondary dépend de step_primary : si primary avait échoué,
    # secondary était blocked. À la reprise, primary réussit ET secondary
    # doit se DÉBLOQUER (les deux repartent).
    _patch_decision(monkeypatch)
    monkeypatch.setattr(
        "app.engine.step_executor.generate_with_ollama",
        lambda model, prompt: (_ for _ in ()).throw(RuntimeError("down")),
    )
    failed = execute_request("explique puis code")
    assert failed["execution_summary"]["status"] == "failed"

    monkeypatch.setattr(
        "app.engine.step_executor.generate_with_ollama", lambda model, prompt: "OK"
    )
    resumed = resume_request(failed["request_id"])
    assert resumed["execution_summary"]["status"] == "success"
    assert resumed["execution_summary"]["successful_step_ids"] == [
        "step_primary", "step_secondary",
    ]


def test_resume_without_checkpoint_raises_lookup(state_env: Path) -> None:
    with pytest.raises(LookupError):
        resume_request("no-such-run")


# ---------------------------------------------------------------------------
# API /resume
# ---------------------------------------------------------------------------

def test_api_resume_404_without_checkpoint(state_env: Path) -> None:
    from fastapi import HTTPException

    from app.main import resume
    from app.schemas import ResumeRequest

    with pytest.raises(HTTPException) as exc_info:
        resume(ResumeRequest(request_id="no-such-run"))
    assert exc_info.value.status_code == 404


def test_api_resume_returns_execute_response(state_env: Path, monkeypatch) -> None:
    from app.main import resume
    from app.schemas import ResumeRequest

    _patch_decision(monkeypatch)
    monkeypatch.setattr(
        "app.engine.step_executor.generate_with_ollama", lambda model, prompt: "OK"
    )
    finished = execute_request("explique puis code")

    response = resume(ResumeRequest(request_id=finished["request_id"]))
    assert response.request_id == finished["request_id"]
    assert response.execution_summary.status == "success"
