"""
Tests du journal d'événements de run (app.engine.run_events).

Invariants couverts :
- Activation par défaut (env absente), désactivation explicite via env.
- Résolution du répertoire : override explicite > dérivation depuis
  BLENDER_OUTPUT_DIR (conteneur read-only) > défaut relatif.
- Écriture JSONL valide, append-only, un fichier par run.
- Non-bloquant : une IO impossible n'élève jamais d'exception.
- Purge par rétention : conservatrice (events.jsonl seul, répertoire
  gardé s'il contient d'autres artefacts, répertoires étrangers intacts).
- Intégration executor : execute_request émet la séquence attendue
  run.started → route.decided → plan.built → step.* → run.finished.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.engine import run_events as revents
from app.engine.executor import execute_request


def _read_events(run_dir: Path) -> list[dict]:
    lines = (run_dir / revents.EVENTS_FILENAME).read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


# ---------------------------------------------------------------------------
# Activation
# ---------------------------------------------------------------------------

def test_enabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(revents.RUN_EVENTS_ENABLED_ENV, raising=False)
    assert revents.is_run_events_enabled() is True


@pytest.mark.parametrize("value", ["0", "false", "FALSE", "No", "off", " false "])
def test_disabled_via_env(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv(revents.RUN_EVENTS_ENABLED_ENV, value)
    assert revents.is_run_events_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "yes", "anything"])
def test_other_values_enable(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv(revents.RUN_EVENTS_ENABLED_ENV, value)
    assert revents.is_run_events_enabled() is True


# ---------------------------------------------------------------------------
# Configuration du répertoire
# ---------------------------------------------------------------------------

def test_default_events_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(revents.RUN_EVENTS_DIR_ENV, raising=False)
    monkeypatch.delenv(revents.BLENDER_OUTPUT_DIR_ENV, raising=False)
    assert revents.get_run_events_dir() == Path(revents.DEFAULT_RUN_EVENTS_DIR)


def test_events_dir_env_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(revents.RUN_EVENTS_DIR_ENV, str(tmp_path / "x"))
    assert revents.get_run_events_dir() == tmp_path / "x"


def test_default_events_dir_derives_from_blender_output_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Conteneur read_only sans override : le défaut doit viser un frère du
    # volume writable (/outputs/blender → /outputs/runs), pas un chemin
    # relatif qui tomberait sur le rootfs en lecture seule (EROFS).
    monkeypatch.delenv(revents.RUN_EVENTS_DIR_ENV, raising=False)
    monkeypatch.setenv(
        revents.BLENDER_OUTPUT_DIR_ENV, str(tmp_path / "outputs" / "blender")
    )
    assert revents.get_run_events_dir() == tmp_path / "outputs" / "runs"


# ---------------------------------------------------------------------------
# Rétention (config)
# ---------------------------------------------------------------------------

def test_retention_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(revents.RUN_EVENTS_RETENTION_DAYS_ENV, raising=False)
    assert (
        revents.get_run_events_retention_days()
        == revents.DEFAULT_RUN_EVENTS_RETENTION_DAYS
    )


@pytest.mark.parametrize("value,expected", [("7", 7), ("0", 0), ("-3", 0), ("abc", 30)])
def test_retention_parsing(
    monkeypatch: pytest.MonkeyPatch, value: str, expected: int
) -> None:
    monkeypatch.setenv(revents.RUN_EVENTS_RETENTION_DAYS_ENV, value)
    assert revents.get_run_events_retention_days() == expected


# ---------------------------------------------------------------------------
# Écriture
# ---------------------------------------------------------------------------

def test_emit_writes_valid_jsonl(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(revents.RUN_EVENTS_ENABLED_ENV, "1")
    monkeypatch.setenv(revents.RUN_EVENTS_DIR_ENV, str(tmp_path))

    revents.emit_run_event(request_id="req-1", kind="run.started", data={"mode": "auto"})
    revents.emit_run_event(request_id="req-1", kind="run.finished")

    events = _read_events(tmp_path / "req-1")
    assert [e["kind"] for e in events] == ["run.started", "run.finished"]
    assert events[0]["request_id"] == "req-1"
    assert events[0]["data"] == {"mode": "auto"}
    assert events[1]["data"] is None
    # ts ISO 8601 UTC parsable
    for event in events:
        datetime.fromisoformat(event["ts"])


def test_emit_separates_runs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(revents.RUN_EVENTS_ENABLED_ENV, "1")
    monkeypatch.setenv(revents.RUN_EVENTS_DIR_ENV, str(tmp_path))

    revents.emit_run_event(request_id="req-a", kind="run.started")
    revents.emit_run_event(request_id="req-b", kind="run.started")

    assert (tmp_path / "req-a" / revents.EVENTS_FILENAME).exists()
    assert (tmp_path / "req-b" / revents.EVENTS_FILENAME).exists()


def test_emit_disabled_writes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(revents.RUN_EVENTS_ENABLED_ENV, "false")
    monkeypatch.setenv(revents.RUN_EVENTS_DIR_ENV, str(tmp_path))

    revents.emit_run_event(request_id="req-1", kind="run.started")

    assert list(tmp_path.iterdir()) == []


def test_emit_never_raises_on_io_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Le "répertoire" racine est un fichier : mkdir échoue → swallow, pas d'exception.
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setenv(revents.RUN_EVENTS_ENABLED_ENV, "1")
    monkeypatch.setenv(revents.RUN_EVENTS_DIR_ENV, str(blocker))

    revents.emit_run_event(request_id="req-1", kind="run.started")  # ne lève pas


# ---------------------------------------------------------------------------
# Purge par rétention
# ---------------------------------------------------------------------------

def _make_run(base: Path, request_id: str, age_days: int) -> Path:
    run_dir = base / request_id
    run_dir.mkdir(parents=True)
    events = run_dir / revents.EVENTS_FILENAME
    events.write_text('{"kind": "run.started"}\n', encoding="utf-8")
    old = datetime.now(timezone.utc) - timedelta(days=age_days)
    os.utime(events, (old.timestamp(), old.timestamp()))
    return run_dir


def test_purge_removes_expired_keeps_recent_and_foreign(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(revents.RUN_EVENTS_ENABLED_ENV, "1")
    monkeypatch.setenv(revents.RUN_EVENTS_DIR_ENV, str(tmp_path))
    monkeypatch.setenv(revents.RUN_EVENTS_RETENTION_DAYS_ENV, "30")

    expired = _make_run(tmp_path, "req-old", age_days=40)
    recent = _make_run(tmp_path, "req-recent", age_days=5)
    # Répertoire étranger sans events.jsonl : jamais touché.
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    (foreign / "state.json").write_text("{}", encoding="utf-8")
    # Run expiré contenant un autre artefact : events purgé, répertoire gardé.
    expired_kept = _make_run(tmp_path, "req-old-kept", age_days=40)
    (expired_kept / "manifest.json").write_text("{}", encoding="utf-8")

    # Premier événement d'un nouveau run → déclenche la purge.
    revents.emit_run_event(request_id="req-new", kind="run.started")

    assert not expired.exists()
    assert (recent / revents.EVENTS_FILENAME).exists()
    assert (foreign / "state.json").exists()
    assert expired_kept.exists()
    assert not (expired_kept / revents.EVENTS_FILENAME).exists()
    assert (expired_kept / "manifest.json").exists()
    assert (tmp_path / "req-new" / revents.EVENTS_FILENAME).exists()


def test_purge_disabled_via_zero_retention(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(revents.RUN_EVENTS_ENABLED_ENV, "1")
    monkeypatch.setenv(revents.RUN_EVENTS_DIR_ENV, str(tmp_path))
    monkeypatch.setenv(revents.RUN_EVENTS_RETENTION_DAYS_ENV, "0")

    expired = _make_run(tmp_path, "req-old", age_days=400)

    revents.emit_run_event(request_id="req-new", kind="run.started")

    assert (expired / revents.EVENTS_FILENAME).exists()


# ---------------------------------------------------------------------------
# Intégration executor
# ---------------------------------------------------------------------------

def test_execute_request_emits_lifecycle_events(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(revents.RUN_EVENTS_ENABLED_ENV, "1")
    monkeypatch.setenv(revents.RUN_EVENTS_DIR_ENV, str(tmp_path))
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
    monkeypatch.setattr(
        "app.engine.step_executor.generate_with_ollama", lambda model, prompt: "OK"
    )

    result = execute_request("écris un script")

    events = _read_events(tmp_path / result["request_id"])
    kinds = [e["kind"] for e in events]
    assert kinds == [
        "run.started",
        "route.decided",
        "plan.built",
        "step.started",
        "step.finished",
        "run.finished",
    ]
    assert all(e["request_id"] == result["request_id"] for e in events)

    started, decided, plan_built = events[0], events[1], events[2]
    assert started["data"]["message"] == "écris un script"
    assert started["data"]["mode"] == "auto"
    assert decided["data"]["task_type"] == "build"
    assert decided["data"]["selected_model"] == "qwen2.5-coder:14b"
    assert plan_built["data"]["steps"][0]["step_id"] == "step_primary"

    step_finished, run_finished = events[4], events[5]
    assert step_finished["data"]["status"] == "success"
    assert step_finished["data"]["error"] is None
    assert run_finished["data"]["execution_summary"]["status"] == "success"
    assert isinstance(run_finished["data"]["duration_ms"], int)


def test_execute_request_emits_step_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(revents.RUN_EVENTS_ENABLED_ENV, "1")
    monkeypatch.setenv(revents.RUN_EVENTS_DIR_ENV, str(tmp_path))
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

    def boom(model, prompt):
        raise RuntimeError("ollama down " + "x" * 5000)

    monkeypatch.setattr("app.engine.step_executor.generate_with_ollama", boom)

    result = execute_request("écris un script")

    events = _read_events(tmp_path / result["request_id"])
    by_kind = {e["kind"]: e for e in events}
    assert by_kind["step.finished"]["data"]["status"] == "error"
    error = by_kind["step.finished"]["data"]["error"]
    assert error is not None
    assert len(error) <= 2000  # tronqué : les événements restent légers
    assert by_kind["run.finished"]["data"]["execution_summary"]["status"] == "failed"
