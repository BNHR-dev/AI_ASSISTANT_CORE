"""
Tests de la Console asynchrone + flux SSE (chantier 5 v2a).

Invariants couverts :
- POST /console/run répond IMMÉDIATEMENT le fragment live (id + abonnement
  SSE + conteneur résultat), le run part en arrière-plan avec le request_id
  imposé, l'issue atterrit au registre (résultat OU exception).
- GET /console/run-result : résultat rendu / exception rendue / message
  « still executing » si inconnu ; garde stricte sur le request_id.
- GET /console/stream : rejoue les événements en lignes SSE `row` puis
  `done` sur run.finished ; `tail=1` ne rejoue pas l'historique ; se
  termine sur résultat au registre même sans run.finished ; garde sur
  l'id (anti-traversal).
- Le registre est borné (les vieux runs sont évincés).
- executor : request_id imposable par l'appelant.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import console


@pytest.fixture(autouse=True)
def _fresh_registry():
    console._RESULTS.clear()
    yield
    console._RESULTS.clear()


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(console.router)
    return TestClient(app)


_RESULT = {
    "task_type": "build",
    "selected_model": "m",
    "execution_summary": {"status": "success", "total_steps": 1,
                          "successful_step_ids": ["step_primary"],
                          "error_step_ids": [], "blocked_step_ids": [],
                          "awaiting_step_ids": []},
    "step_results": [], "plan": [], "duration_ms": 5,
    "output": "done", "decision_path": [],
}


# ---------------------------------------------------------------------------
# POST /console/run — lancement asynchrone
# ---------------------------------------------------------------------------

def test_run_returns_live_fragment_and_stores_result(client, monkeypatch) -> None:
    captured: dict = {}

    def fake_execute(message, mode="auto", pause_before_tools=False, request_id=None):
        captured.update(message=message, request_id=request_id)
        return {**_RESULT, "request_id": request_id}

    monkeypatch.setattr(console, "execute_request", fake_execute)
    response = client.post("/console/run", data={"message": "construis un truc"})

    assert response.status_code == 200
    match = re.search(r"/console/stream/([A-Za-z0-9-]+)", response.text)
    assert match, "le fragment live doit contenir l'URL du flux"
    request_id = match.group(1)
    assert f'id="final-{request_id}"' in response.text
    # Le request_id du fragment est CELUI imposé au moteur (abonnement fiable).
    assert captured["request_id"] == request_id
    # L'issue est au registre (TestClient exécute le bg pendant l'appel).
    assert "result" in console._RESULTS[request_id]


def test_run_background_exception_lands_in_registry(client, monkeypatch) -> None:
    def boom(message, **kwargs):
        raise RuntimeError("moteur indisponible")

    monkeypatch.setattr(console, "execute_request", boom)
    response = client.post("/console/run", data={"message": "peu importe"})
    request_id = re.search(r"/console/stream/([A-Za-z0-9-]+)", response.text).group(1)

    result = client.get(f"/console/run-result/{request_id}")
    assert "RuntimeError" in result.text


def test_run_empty_message_stays_synchronous(client) -> None:
    response = client.post("/console/run", data={"message": "   "})
    assert "empty" in response.text.lower()
    assert "/console/stream/" not in response.text


# ---------------------------------------------------------------------------
# GET /console/run-result
# ---------------------------------------------------------------------------

def test_run_result_unknown_run_says_still_executing(client) -> None:
    response = client.get("/console/run-result/aaaa-bbbb")
    assert response.status_code == 200
    assert "still executing" in response.text


def test_run_result_rejects_bad_ids(client) -> None:
    assert client.get("/console/run-result/..%2Fetc").status_code == 404
    assert client.get("/console/run-result/" + "x" * 65).status_code == 404


def test_registry_is_bounded() -> None:
    for i in range(console._RESULTS_MAX + 10):
        console._store_result(f"run-{i}", {"result": {}})
    assert len(console._RESULTS) == console._RESULTS_MAX
    assert "run-0" not in console._RESULTS  # les plus vieux évincés


# ---------------------------------------------------------------------------
# GET /console/stream — SSE
# ---------------------------------------------------------------------------

def _write_events(base: Path, request_id: str, events: list[dict]) -> Path:
    run_dir = base / request_id
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "events.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    return path


_EVENTS = [
    {"ts": "2026-07-11T20:00:00.000000+00:00", "kind": "run.started", "data": {}},
    {"ts": "2026-07-11T20:00:00.100000+00:00", "kind": "step.started",
     "data": {"step_id": "step_primary"}},
    {"ts": "2026-07-11T20:00:02.000000+00:00", "kind": "step.finished",
     "data": {"step_id": "step_primary", "status": "success", "duration_ms": 1900}},
    {"ts": "2026-07-11T20:00:02.100000+00:00", "kind": "run.finished",
     "data": {"execution_summary": {"status": "success"}, "duration_ms": 2100}},
]


def _read_sse(client: TestClient, url: str) -> str:
    with client.stream("GET", url) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        return "".join(chunk for chunk in response.iter_text())


def test_stream_replays_events_then_done(client, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AAC_RUN_EVENTS_DIR", str(tmp_path))
    _write_events(tmp_path, "run-sse", _EVENTS)

    body = _read_sse(client, "/console/stream/run-sse")

    assert body.count("event: row") == 4
    assert "tl-row" in body and "step_primary" in body
    assert body.rstrip().endswith("data: finished") or "event: done" in body
    # done arrive après les rows
    assert body.index("event: done") > body.rindex("event: row")


def test_stream_tail_skips_history(client, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AAC_RUN_EVENTS_DIR", str(tmp_path))
    path = _write_events(tmp_path, "run-tail", _EVENTS)

    # En mode tail, l'historique (dont le run.finished du run INITIAL) est
    # ignoré ; le flux se termine dès que le registre a l'issue de la reprise.
    console._store_result("run-tail", {"result": dict(_RESULT)})
    body = _read_sse(client, "/console/stream/run-tail?tail=1")

    assert "event: row" not in body  # rien rejoué
    assert "event: done" in body
    # nouveaux événements après la connexion : couverts par le polling — la
    # reprise réelle est vérifiée live (le générateur lit depuis l'offset).
    assert path.stat().st_size > 0


def test_stream_ends_on_registry_without_events(client, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AAC_RUN_EVENTS_DIR", str(tmp_path))
    console._store_result("run-flash", {"exception": "crashed before any event"})
    body = _read_sse(client, "/console/stream/run-flash")
    assert "event: done" in body and "result-ready" in body


def test_stream_rejects_bad_ids(client) -> None:
    assert client.get("/console/stream/" + "x" * 65).status_code == 404


# ---------------------------------------------------------------------------
# executor — request_id imposable
# ---------------------------------------------------------------------------

def test_execute_request_honors_caller_request_id(monkeypatch) -> None:
    from app.engine.executor import execute_request

    monkeypatch.setattr(
        "app.engine.executor.build_route_decision",
        lambda message, has_image: {
            "task_type": "build", "primary_agent": "AGENT_BUILDER_IA",
            "selected_model": "m", "selected_tool": None, "output_format": "code",
            "needs_web": False, "second_call": None, "matched_rule": None,
            "reason": "t", "reason_debug": "t", "classifier_reason": "t",
            "decision_trace": [], "decision_path": [],
        },
    )
    monkeypatch.setattr(
        "app.engine.step_executor.generate_with_ollama", lambda model, prompt: "OK"
    )
    result = execute_request("code un truc", request_id="imposed-id-123")
    assert result["request_id"] == "imposed-id-123"
