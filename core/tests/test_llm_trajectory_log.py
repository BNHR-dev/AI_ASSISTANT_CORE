"""
H.6.1 — Tests du journal de trajectoires LLM.

Invariants couverts :
- Activation par défaut (env absente).
- Désactivation explicite via env.
- Écriture JSONL valide, une ligne par appel.
- Création automatique du répertoire.
- Non-bloquant : une IO impossible n'élève jamais d'exception.
- Format des champs (ts ISO, ir sérialisé, fallback bool).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.engine import llm_trajectory_log as tlog


def _read_jsonl(path: Path) -> list[dict]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def _only_log_file(dir_: Path) -> Path:
    files = sorted(dir_.glob("*.jsonl"))
    assert len(files) == 1, f"expected exactly one log file, got {files}"
    return files[0]


# ---------------------------------------------------------------------------
# Activation
# ---------------------------------------------------------------------------

def test_enabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(tlog.TRAJECTORY_LOG_ENABLED_ENV, raising=False)
    assert tlog.is_trajectory_logging_enabled() is True


@pytest.mark.parametrize("value", ["0", "false", "FALSE", "No", "off", " false "])
def test_disabled_via_env(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv(tlog.TRAJECTORY_LOG_ENABLED_ENV, value)
    assert tlog.is_trajectory_logging_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "yes", "anything"])
def test_other_values_enable(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv(tlog.TRAJECTORY_LOG_ENABLED_ENV, value)
    assert tlog.is_trajectory_logging_enabled() is True


# ---------------------------------------------------------------------------
# Configuration du répertoire
# ---------------------------------------------------------------------------

def test_default_log_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(tlog.TRAJECTORY_LOG_DIR_ENV, raising=False)
    assert tlog.get_trajectory_log_dir() == Path(tlog.DEFAULT_TRAJECTORY_LOG_DIR)


def test_log_dir_env_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(tlog.TRAJECTORY_LOG_DIR_ENV, str(tmp_path / "x"))
    assert tlog.get_trajectory_log_dir() == tmp_path / "x"


# ---------------------------------------------------------------------------
# Écriture
# ---------------------------------------------------------------------------

def test_write_creates_directory_and_jsonl_line(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "trajectories"
    monkeypatch.setenv(tlog.TRAJECTORY_LOG_DIR_ENV, str(target))
    monkeypatch.delenv(tlog.TRAJECTORY_LOG_ENABLED_ENV, raising=False)

    tlog.log_trajectory(
        stage="extractor",
        model="qwen2.5-coder:7b",
        prompt="user prompt",
        raw_response='{"ok": true}',
        parse_ok=True,
        ir={"schema_version": "v0", "subject": {"kind": "bottle"}},
        fallback=False,
        error=None,
        request_id="req-123",
    )

    assert target.is_dir()
    records = _read_jsonl(_only_log_file(target))
    assert len(records) == 1
    rec = records[0]
    assert rec["stage"] == "extractor"
    assert rec["model"] == "qwen2.5-coder:7b"
    assert rec["prompt"] == "user prompt"
    assert rec["raw_response"] == '{"ok": true}'
    assert rec["parse_ok"] is True
    assert rec["ir"] == {"schema_version": "v0", "subject": {"kind": "bottle"}}
    assert rec["fallback"] is False
    assert rec["error"] is None
    assert rec["request_id"] == "req-123"
    # ts présent et parseable
    assert isinstance(rec["ts"], str) and "T" in rec["ts"]


def test_multiple_calls_append(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(tlog.TRAJECTORY_LOG_DIR_ENV, str(tmp_path))
    monkeypatch.delenv(tlog.TRAJECTORY_LOG_ENABLED_ENV, raising=False)

    for i in range(3):
        tlog.log_trajectory(
            stage="script_gen",
            model="qwen2.5-coder:7b",
            prompt=f"p{i}",
            raw_response=f"r{i}",
        )

    records = _read_jsonl(_only_log_file(tmp_path))
    assert [r["prompt"] for r in records] == ["p0", "p1", "p2"]


def test_fallback_record(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(tlog.TRAJECTORY_LOG_DIR_ENV, str(tmp_path))
    monkeypatch.delenv(tlog.TRAJECTORY_LOG_ENABLED_ENV, raising=False)

    tlog.log_trajectory(
        stage="extractor",
        model="qwen2.5-coder:7b",
        prompt="bad input",
        raw_response=None,
        parse_ok=False,
        ir=None,
        fallback=True,
        error="llm_call_error: TimeoutError: ...",
    )

    rec = _read_jsonl(_only_log_file(tmp_path))[0]
    assert rec["fallback"] is True
    assert rec["parse_ok"] is False
    assert rec["raw_response"] is None
    assert rec["ir"] is None
    assert rec["error"].startswith("llm_call_error:")


def test_disabled_writes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(tlog.TRAJECTORY_LOG_DIR_ENV, str(tmp_path))
    monkeypatch.setenv(tlog.TRAJECTORY_LOG_ENABLED_ENV, "false")

    tlog.log_trajectory(
        stage="extractor",
        model="qwen2.5-coder:7b",
        prompt="x",
        raw_response="y",
    )

    assert list(tmp_path.glob("*.jsonl")) == []


# ---------------------------------------------------------------------------
# Non-blocking
# ---------------------------------------------------------------------------

def test_io_failure_is_swallowed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # On force mkdir à exploser ; log_trajectory ne doit PAS lever.
    monkeypatch.setenv(tlog.TRAJECTORY_LOG_DIR_ENV, str(tmp_path / "x"))
    monkeypatch.delenv(tlog.TRAJECTORY_LOG_ENABLED_ENV, raising=False)

    def boom(self, *a, **kw):  # noqa: ANN001
        raise OSError("simulated disk failure")

    monkeypatch.setattr(Path, "mkdir", boom)

    # Doit retourner None sans exception.
    tlog.log_trajectory(
        stage="extractor",
        model="qwen2.5-coder:7b",
        prompt="x",
        raw_response="y",
    )


def test_json_dumps_failure_is_swallowed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(tlog.TRAJECTORY_LOG_DIR_ENV, str(tmp_path))
    monkeypatch.delenv(tlog.TRAJECTORY_LOG_ENABLED_ENV, raising=False)

    class Unjsonable:
        def __repr__(self) -> str:
            raise RuntimeError("nope")

    # extra contient un objet dont la sérialisation peut échouer.
    # default=str dans le module devrait absorber la plupart des cas, mais
    # même un échec total ne doit pas remonter.
    tlog.log_trajectory(
        stage="extractor",
        model="qwen2.5-coder:7b",
        prompt="x",
        raw_response="y",
        extra={"weird": Unjsonable()},
    )
    # Pas d'assertion : la garantie est "n'a pas levé".
