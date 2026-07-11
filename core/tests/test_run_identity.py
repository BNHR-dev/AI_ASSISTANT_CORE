"""
Tests du contrat canonique des request_id (app.engine.run_identity).

Un request_id nomme un dossier sur disque : chaque surface qui en accepte
un (API /resume, Console, run_events, run_state, rejeu ComfyUI) doit
appliquer LE MÊME contrat. Invariants couverts :

- charset strict ^[A-Za-z0-9-]{1,64}$ : uuid4 acceptés, tout séparateur de
  chemin refusé (traversal, chemins absolus, backslashes Windows,
  encodages ambigus, ids vides ou trop longs) ;
- resolve_run_dir refuse ce qui sort de la racine (ceinture symlink) ;
- run_events / run_state n'écrivent RIEN hors racine sur id invalide,
  sans lever (invariant non-bloquant conservé) ;
- load_run_state → None sur id invalide → /resume répond 404 côté API,
  et un id non conforme au schéma est rejeté 422 dès pydantic ;
- reproduce_comfyui n'utilise jamais un request_id de manifest non validé
  pour nommer le dossier de rejeu ni le filename_prefix ;
- la Console partage la même regex (pas de duplication).
"""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from app.engine import run_events as revents
from app.engine import run_state as rstate
from app.engine.run_identity import is_valid_request_id, resolve_run_dir

ADVERSARIAL_IDS = [
    "",  # vide
    "../evil",  # traversal relatif
    "..",  # dossier parent seul
    "/etc/passwd",  # chemin absolu
    "a/b",  # séparateur POSIX
    "a\\b",  # séparateur Windows
    "..\\..\\evil",  # traversal Windows
    "run id",  # espace
    "run.id",  # point (composants ambigus)
    "%2e%2e%2fevil",  # encodage URL (le % est hors charset)
    "run\x00id",  # octet nul
    "req-1\n",  # newline final (piège du $ de re.match)
    "é-run",  # hors ASCII
    "a" * 65,  # trop long
]

VALID_IDS = [
    str(uuid.uuid4()),
    "req-1",
    "ABC-123",
    "a",
    "a" * 64,
]


# ---------------------------------------------------------------------------
# Contrat de base
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value", VALID_IDS)
def test_valid_ids_accepted(value: str) -> None:
    assert is_valid_request_id(value) is True


@pytest.mark.parametrize("value", ADVERSARIAL_IDS)
def test_adversarial_ids_rejected(value: str) -> None:
    assert is_valid_request_id(value) is False


def test_non_strings_rejected() -> None:
    for value in (None, 42, ["req-1"], {"id": "req-1"}):
        assert is_valid_request_id(value) is False


@pytest.mark.parametrize("value", ADVERSARIAL_IDS)
def test_resolve_run_dir_rejects_adversarial(tmp_path: Path, value: str) -> None:
    assert resolve_run_dir(tmp_path, value) is None


def test_resolve_run_dir_returns_path_under_base(tmp_path: Path) -> None:
    resolved = resolve_run_dir(tmp_path, "req-1")
    assert resolved == tmp_path / "req-1"


def test_resolve_run_dir_rejects_symlink_escape(tmp_path: Path) -> None:
    # Un id valide dont le dossier est un symlink sortant de la racine :
    # le charset ne suffit plus, la résolution doit refuser.
    base = tmp_path / "runs"
    base.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (base / "escape").symlink_to(outside)
    assert resolve_run_dir(base, "escape") is None


# ---------------------------------------------------------------------------
# run_events / run_state : rien hors racine, jamais d'exception
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_id", ["../evil", "a/b", "a\\b"])
def test_emit_run_event_refuses_invalid_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, bad_id: str
) -> None:
    base = tmp_path / "runs"
    base.mkdir()
    monkeypatch.setenv(revents.RUN_EVENTS_ENABLED_ENV, "1")
    monkeypatch.setenv(revents.RUN_EVENTS_DIR_ENV, str(base))

    revents.emit_run_event(request_id=bad_id, kind="run.started")  # ne lève pas

    assert list(base.iterdir()) == []
    assert not (tmp_path / "evil").exists()


@pytest.mark.parametrize("bad_id", ["../evil", "a/b", "a\\b"])
def test_save_run_state_refuses_invalid_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, bad_id: str
) -> None:
    from app.engine.planner_types import ExecutionPlan

    base = tmp_path / "runs"
    base.mkdir()
    monkeypatch.setenv("AAC_RUN_STATE_ENABLED", "1")
    monkeypatch.setenv(revents.RUN_EVENTS_DIR_ENV, str(base))

    rstate.save_run_state(
        bad_id,
        message="m",
        has_image=False,
        mode="auto",
        decision={"task_type": "build"},
        plan=ExecutionPlan(task_type="build", steps=[]),
        step_results=[],
    )  # ne lève pas

    assert list(base.iterdir()) == []
    assert not (tmp_path / "evil").exists()


def test_load_run_state_refuses_invalid_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Même si un fichier existe au chemin traversé, un id invalide → None.
    monkeypatch.setenv(revents.RUN_EVENTS_DIR_ENV, str(tmp_path / "runs"))
    outside = tmp_path / "evil"
    outside.mkdir()
    (outside / rstate.STATE_FILENAME).write_text(
        '{"message": "m", "decision": {}, "plan": {}}', encoding="utf-8"
    )
    assert rstate.load_run_state("../evil") is None


# ---------------------------------------------------------------------------
# Surfaces : API /resume et Console partagent le contrat
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_id", ["../evil", "/etc/passwd", "", "a" * 65])
def test_api_resume_rejects_invalid_id(bad_id: str) -> None:
    from fastapi.testclient import TestClient

    from app.main import create_app

    client = TestClient(create_app())
    response = client.post("/resume", json={"request_id": bad_id})
    assert response.status_code == 422  # rejeté par le schéma, avant tout IO


def test_api_resume_valid_unknown_id_is_404(tmp_path: Path, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from app.main import create_app

    monkeypatch.setenv(revents.RUN_EVENTS_DIR_ENV, str(tmp_path))
    client = TestClient(create_app())
    response = client.post("/resume", json={"request_id": "no-such-run"})
    assert response.status_code == 404


def test_console_uses_canonical_contract() -> None:
    """Le module console n'embarque plus sa propre regex : il importe le
    contrat canonique (pas de duplication qui pourrait dériver)."""
    import inspect

    import console

    assert console.is_valid_request_id is is_valid_request_id
    assert "A-Za-z0-9" not in inspect.getsource(console)


@pytest.mark.parametrize("route", ["/console/stream/{bad}", "/console/run-result/{bad}"])
def test_console_routes_reject_invalid_ids(route: str) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import console

    app = FastAPI()
    app.include_router(console.router)
    client = TestClient(app)
    response = client.get(route.format(bad="req%0A"))  # "req\n"
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Rejeu ComfyUI : le request_id du manifest client est validé
# ---------------------------------------------------------------------------

def test_reproduce_comfyui_sanitizes_manifest_request_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from app.engine.reproduce import reproduce_comfyui

    captured: dict = {}

    def fake_queue(workflow):
        captured["workflow"] = workflow
        return "prompt-1"

    monkeypatch.setattr(
        "app.clients.comfyui_client.COMFYUI_OUTPUT_DIR", str(tmp_path / "out")
    )
    monkeypatch.setattr("app.clients.comfyui_client.free_execution_cache", lambda: None)
    monkeypatch.setattr("app.clients.comfyui_client.queue_prompt", fake_queue)
    monkeypatch.setattr("app.clients.comfyui_client.wait_for_completion", lambda pid: {})
    monkeypatch.setattr(
        "app.clients.comfyui_client.extract_output_file", lambda history: (None, None)
    )
    monkeypatch.setattr(
        "app.clients.comfyui_client.get_comfyui_system_info", lambda: {}
    )

    from app.engine import repro as repro_utils

    workflow = {"9": {"inputs": {"filename_prefix": "orig"}}}
    manifest = {
        "request_id": "../../evil",
        "repro": {
            "variants": [
                {"index": 1, "workflow_sha256": repro_utils.sha256_canonical_json(workflow)}
            ]
        },
    }

    report = reproduce_comfyui(manifest, {1: workflow})

    # Aucun dossier créé hors racine, repli neutre "unknown" partout.
    # ("../../evil" depuis out/repro/ aboutirait à tmp_path/evil.)
    assert not (tmp_path / "evil").exists()
    assert report["reproduced_request_id"] == "unknown"
    prefix = captured["workflow"]["9"]["inputs"]["filename_prefix"]
    assert prefix.startswith("repro/unknown/")
    replay_root = tmp_path / "out" / "repro"
    assert list(replay_root.iterdir()) == [replay_root / "unknown"]
