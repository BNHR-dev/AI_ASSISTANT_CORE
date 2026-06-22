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
    monkeypatch.delenv(tlog.BLENDER_OUTPUT_DIR_ENV, raising=False)
    assert tlog.get_trajectory_log_dir() == Path(tlog.DEFAULT_TRAJECTORY_LOG_DIR)


def test_log_dir_env_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(tlog.TRAJECTORY_LOG_DIR_ENV, str(tmp_path / "x"))
    assert tlog.get_trajectory_log_dir() == tmp_path / "x"


def test_default_log_dir_derives_from_blender_output_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Mode Y (conteneur read_only), sans override explicite : le défaut doit viser le
    # volume writable BLENDER_OUTPUT_DIR (/outputs/blender), pas un chemin relatif qui
    # tomberait sur le rootfs en lecture seule (EROFS).
    monkeypatch.delenv(tlog.TRAJECTORY_LOG_DIR_ENV, raising=False)
    monkeypatch.setenv(tlog.BLENDER_OUTPUT_DIR_ENV, str(tmp_path / "outputs" / "blender"))
    assert (
        tlog.get_trajectory_log_dir()
        == tmp_path / "outputs" / "blender" / "_trajectories"
    )


def test_explicit_log_dir_beats_blender_output_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(tlog.TRAJECTORY_LOG_DIR_ENV, str(tmp_path / "explicit"))
    monkeypatch.setenv(tlog.BLENDER_OUTPUT_DIR_ENV, str(tmp_path / "blender"))
    assert tlog.get_trajectory_log_dir() == tmp_path / "explicit"


def test_write_lands_under_blender_output_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Écriture réelle : avec BLENDER_OUTPUT_DIR seul, la ligne JSONL atterrit sous
    # <BLENDER_OUTPUT_DIR>/_trajectories (writable) — prouve l'absence d'EROFS.
    monkeypatch.delenv(tlog.TRAJECTORY_LOG_DIR_ENV, raising=False)
    monkeypatch.delenv(tlog.TRAJECTORY_LOG_ENABLED_ENV, raising=False)
    out = tmp_path / "outputs" / "blender"
    monkeypatch.setenv(tlog.BLENDER_OUTPUT_DIR_ENV, str(out))
    tlog.log_trajectory(
        stage="script_gen", model="qwen2.5-coder:7b", prompt="p", raw_response="r"
    )
    files = list((out / "_trajectories").glob("*.jsonl"))
    assert len(files) == 1
    assert files[0].read_text(encoding="utf-8").strip()


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


# ---------------------------------------------------------------------------
# C3 (audit 2026-06-10) — Rétention / purge
# ---------------------------------------------------------------------------

def test_default_retention_days(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(tlog.TRAJECTORY_RETENTION_DAYS_ENV, raising=False)
    assert tlog.get_trajectory_retention_days() == tlog.DEFAULT_TRAJECTORY_RETENTION_DAYS


@pytest.mark.parametrize("value,expected", [("7", 7), ("0", 0), ("-3", 0), ("abc", 30)])
def test_retention_env_parsing(
    monkeypatch: pytest.MonkeyPatch, value: str, expected: int
) -> None:
    monkeypatch.setenv(tlog.TRAJECTORY_RETENTION_DAYS_ENV, value)
    assert tlog.get_trajectory_retention_days() == expected


def test_purge_removes_expired_files_on_write(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv(tlog.TRAJECTORY_LOG_ENABLED_ENV, raising=False)
    monkeypatch.setenv(tlog.TRAJECTORY_LOG_DIR_ENV, str(tmp_path))
    monkeypatch.setenv(tlog.TRAJECTORY_RETENTION_DAYS_ENV, "30")
    old = tmp_path / "2020-01-01.jsonl"
    old.write_text("{}\n", encoding="utf-8")
    recent = tmp_path / f"{tlog._utc_now().strftime('%Y-%m-%d')}.jsonl"
    weird = tmp_path / "notes.jsonl"  # nom non conforme : jamais purgé
    weird.write_text("keep\n", encoding="utf-8")
    tlog.log_trajectory(stage="t", model="m", prompt="p", raw_response="r")
    assert not old.exists()
    assert recent.exists()
    assert weird.exists()


def test_purge_disabled_with_zero_retention(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv(tlog.TRAJECTORY_LOG_ENABLED_ENV, raising=False)
    monkeypatch.setenv(tlog.TRAJECTORY_LOG_DIR_ENV, str(tmp_path))
    monkeypatch.setenv(tlog.TRAJECTORY_RETENTION_DAYS_ENV, "0")
    old = tmp_path / "2020-01-01.jsonl"
    old.write_text("{}\n", encoding="utf-8")
    tlog.log_trajectory(stage="t", model="m", prompt="p", raw_response="r")
    assert old.exists()
