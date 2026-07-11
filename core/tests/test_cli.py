"""Tests du CLI `aac` (core/cli.py) — CliRunner + httpx.MockTransport.

Aucun serveur : `cli.make_client` est substitué par un client monté sur un
MockTransport qui rejoue des réponses API réalistes et capture les requêtes
émises (URL, en-têtes, corps). Couvre : rendu humain, --json, envoi du
bearer token, --base-url, --image, et le contrat des codes de sortie
(0 OK / 1 erreur API / 2 injoignable / 3 auth refusée).
"""

from __future__ import annotations

import json

import httpx
import pytest
from click.testing import CliRunner

import cli

# ---------------------------------------------------------------------------
# Réponses API réalistes (formes alignées sur app/schemas.py)
# ---------------------------------------------------------------------------

HEALTH_OK = {"status": "ok"}

RUNTIME_OK = {
    "status": "ok",
    "version": "1.7.0",
    "checked_at": "2026-07-07T10:00:00Z",
    "summary": "core runtime ok",
    "services": {
        "ollama": {
            "name": "ollama", "ready": True, "required": True, "role": "llm",
            "reason": "reachable", "endpoint": "http://127.0.0.1:11434",
            "activity": None, "reachable": True, "missing": [],
        },
        "comfyui": {
            "name": "comfyui", "ready": False, "required": False, "role": "image",
            "reason": "modèles absents", "endpoint": None,
            "activity": None, "reachable": True, "missing": ["realvis.safetensors"],
        },
    },
}

CANONICAL_OK = {
    "status": "ok",
    "version": "1.7.0",
    "canonical_paths": ["core/app"],
    "legacy_shims": ["core/router_service.py"],
    "active_runtime_modules": ["app.main", "app.engine.executor"],
    "active_auxiliary_modules": ["console"],
    "optional_runtime_services": ["comfyui"],
    "dormant_modules": [],
    "rule": "app/* prime sur les shims racine",
}

ROUTE_DECISION = {
    "task_type": "image_generation",
    "primary_agent": "creative",
    "selected_model": "pixtral",
    "needs_web": False,
    "second_call": None,
    "output_format": "image",
    "selected_tool": "comfyui",
    "matched_rule": "image_keywords",
    "reason_debug": None,
    "classifier_reason": "mots-clés image",
    "decision_trace": ["classifier → image_generation", "final_tool → comfyui"],
    "decision_path": ["classifier → image_generation", "final_tool → comfyui"],
    "reason": "demande de génération d'image",
}

EXECUTE_OK = {
    **ROUTE_DECISION,
    "execution_strategy": "plan",
    "execution_summary": {
        "status": "success", "total_steps": 2,
        "successful_step_ids": ["step_1", "step_2"],
        "error_step_ids": [], "blocked_step_ids": [],
    },
    "request_id": "req-123",
    "duration_ms": 4321,
    "plan": [
        {"step_id": "step_1", "step_type": "llm_call", "goal": "préparer le prompt",
         "agent": "creative", "model": "pixtral", "tool": None,
         "depends_on": [], "status": "success"},
        {"step_id": "step_2", "step_type": "tool_call", "goal": "générer l'image",
         "agent": None, "model": None, "tool": "comfyui",
         "depends_on": ["step_1"], "status": "success"},
    ],
    "step_results": [
        {"step_id": "step_1", "step_type": "llm_call", "status": "success",
         "output": "ok", "error": None, "meta": {}, "duration_ms": 1200},
        {"step_id": "step_2", "step_type": "tool_call", "status": "success",
         "output": None, "error": None, "meta": {}, "duration_ms": 3100},
    ],
    "artifact_paths": ["outputs/comfyui/img_1.png"],
    "artifact_filenames": ["img_1.png"],
    "output": "Image générée.",
}

HEALTH_ROUTES = {
    ("GET", "/health"): HEALTH_OK,
    ("GET", "/health/runtime"): RUNTIME_OK,
    ("GET", "/debug/canonical"): CANONICAL_OK,
}


class FakeAPI:
    """Handler MockTransport : rejoue `responses` et capture les requêtes.

    Une valeur int dans `responses` est rejouée comme code HTTP d'erreur.
    """

    def __init__(self, responses: dict):
        self.responses = responses
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        entry = self.responses.get((request.method, request.url.path))
        if entry is None:
            return httpx.Response(404, json={"detail": "not found"})
        if isinstance(entry, int):
            return httpx.Response(entry, json={"detail": "erreur"})
        return httpx.Response(200, json=entry)


def _install(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    """Substitue cli.make_client par un client monté sur le transport mocké."""
    def fake_make_client(base_url, token, read_timeout):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        return httpx.Client(
            base_url=base_url, headers=headers,
            transport=httpx.MockTransport(handler),
        )
    monkeypatch.setattr(cli, "make_client", fake_make_client)


@pytest.fixture
def runner(monkeypatch: pytest.MonkeyPatch) -> CliRunner:
    # Isole les tests d'un éventuel token posé dans l'environnement réel.
    monkeypatch.delenv("AAC_API_TOKEN", raising=False)
    return CliRunner()


# ---------------------------------------------------------------------------
# aac health
# ---------------------------------------------------------------------------

def test_health_ok(runner, monkeypatch):
    api = FakeAPI(HEALTH_ROUTES)
    _install(monkeypatch, api)

    result = runner.invoke(cli.aac, ["health"])

    assert result.exit_code == 0
    assert "ollama" in result.output
    assert "requis, llm" in result.output
    assert "manquants : realvis.safetensors" in result.output
    assert "core runtime ok" in result.output
    assert "shims legacy" in result.output


def test_health_json_pipe(runner, monkeypatch):
    api = FakeAPI(HEALTH_ROUTES)
    _install(monkeypatch, api)

    result = runner.invoke(cli.aac, ["--json", "health"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["health"] == HEALTH_OK
    assert payload["runtime"] == RUNTIME_OK
    assert payload["canonical"] == CANONICAL_OK


def test_health_degraded_exit_code(runner, monkeypatch):
    degraded = {**RUNTIME_OK, "status": "degraded",
                "summary": "core runtime degraded: ollama"}
    api = FakeAPI({**HEALTH_ROUTES, ("GET", "/health/runtime"): degraded})
    _install(monkeypatch, api)

    result = runner.invoke(cli.aac, ["health"])

    assert result.exit_code == cli.EXIT_API_ERROR
    assert "degraded" in result.output


def test_health_unreachable_exit_code(runner, monkeypatch):
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connexion refusée", request=request)
    _install(monkeypatch, refuse)

    result = runner.invoke(cli.aac, ["health"])

    assert result.exit_code == cli.EXIT_UNREACHABLE
    assert "injoignable" in result.stderr


def test_health_401_exit_code(runner, monkeypatch):
    api = FakeAPI({**HEALTH_ROUTES, ("GET", "/health/runtime"): 401})
    _install(monkeypatch, api)

    result = runner.invoke(cli.aac, ["health"])

    assert result.exit_code == cli.EXIT_AUTH
    assert "AAC_API_TOKEN" in result.stderr


def test_token_flag_sends_bearer(runner, monkeypatch):
    api = FakeAPI(HEALTH_ROUTES)
    _install(monkeypatch, api)

    result = runner.invoke(cli.aac, ["--token", "secret-token-0123456789", "health"])

    assert result.exit_code == 0
    assert all(
        req.headers.get("Authorization") == "Bearer secret-token-0123456789"
        for req in api.requests
    )


def test_base_url_override(runner, monkeypatch):
    api = FakeAPI(HEALTH_ROUTES)
    _install(monkeypatch, api)

    result = runner.invoke(cli.aac, ["--base-url", "http://127.0.0.1:9999", "health"])

    assert result.exit_code == 0
    assert all(req.url.port == 9999 for req in api.requests)


# ---------------------------------------------------------------------------
# aac inspect
# ---------------------------------------------------------------------------

def test_inspect_renders_decision_and_trace(runner, monkeypatch):
    api = FakeAPI({("POST", "/route"): ROUTE_DECISION})
    _install(monkeypatch, api)

    result = runner.invoke(cli.aac, ["inspect", "génère une image de théière"])

    assert result.exit_code == 0
    sent = json.loads(api.requests[0].content)
    assert sent == {"message": "génère une image de théière", "has_image": False}
    assert "image_generation" in result.output
    assert "comfyui" in result.output
    assert "└─ final_tool → comfyui" in result.output


def test_inspect_image_flag_sets_has_image(runner, monkeypatch):
    api = FakeAPI({("POST", "/route"): ROUTE_DECISION})
    _install(monkeypatch, api)

    result = runner.invoke(cli.aac, ["inspect", "--image", "décris cette image"])

    assert result.exit_code == 0
    sent = json.loads(api.requests[0].content)
    assert sent["has_image"] is True


def test_inspect_json_is_raw_response(runner, monkeypatch):
    api = FakeAPI({("POST", "/route"): ROUTE_DECISION})
    _install(monkeypatch, api)

    result = runner.invoke(cli.aac, ["--json", "inspect", "une théière"])

    assert result.exit_code == 0
    assert json.loads(result.output) == ROUTE_DECISION


# ---------------------------------------------------------------------------
# aac execute
# ---------------------------------------------------------------------------

def test_execute_success_renders_plan_and_artifacts(runner, monkeypatch):
    api = FakeAPI({("POST", "/execute"): EXECUTE_OK})
    _install(monkeypatch, api)

    result = runner.invoke(cli.aac, ["execute", "génère une image de théière"])

    assert result.exit_code == 0
    assert "✔ step_1" in result.output
    assert "✔ step_2" in result.output
    assert "(2/2 étapes OK)" in result.output
    assert "outputs/comfyui/img_1.png" in result.output
    assert "Image générée." in result.output


def test_execute_failure_exit_code_and_error_line(runner, monkeypatch):
    failed = {
        **EXECUTE_OK,
        "execution_summary": {
            "status": "failed", "total_steps": 2,
            "successful_step_ids": ["step_1"],
            "error_step_ids": ["step_2"], "blocked_step_ids": [],
        },
        "step_results": [
            EXECUTE_OK["step_results"][0],
            {**EXECUTE_OK["step_results"][1], "status": "error",
             "error": "comfyui timeout"},
        ],
        "artifact_paths": [],
        "output": "Échec de la génération.",
    }
    api = FakeAPI({("POST", "/execute"): failed})
    _install(monkeypatch, api)

    result = runner.invoke(cli.aac, ["execute", "génère une image"])

    assert result.exit_code == cli.EXIT_API_ERROR
    assert "✘ step_2" in result.output
    assert "erreur : comfyui timeout" in result.output


def test_execute_json_is_raw_response(runner, monkeypatch):
    api = FakeAPI({("POST", "/execute"): EXECUTE_OK})
    _install(monkeypatch, api)

    result = runner.invoke(cli.aac, ["--json", "execute", "une théière"])

    assert result.exit_code == 0
    assert json.loads(result.output) == EXECUTE_OK


# ---------------------------------------------------------------------------
# aac reproduce
# ---------------------------------------------------------------------------

REPRODUCE_EXACT = {
    "pipeline": "comfyui",
    "verdict": "exact",
    "dhash_threshold": 4,
    "reproduced_request_id": "orig-run",
    "variants": [{"index": 1, "verdict": "exact", "image": {"dhash_distance": 0}}],
    "checks": [],
    "environment_diffs": [],
    "report_path": "/outputs/comfyui/repro/orig-run/abc/reproduce_report.json",
    "duration_ms": 42000,
}


def _make_comfyui_run(tmp_path):
    run_dir = tmp_path / "orig-run"
    run_dir.mkdir()
    workflow = {"9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "orig-run/x"}}}
    (run_dir / "workflow_resolved_v1.json").write_text(json.dumps(workflow), encoding="utf-8")
    manifest = {
        "manifest_version": 2,
        "pipeline": "comfyui",
        "request_id": "orig-run",
        "repro": {"variants": [{"index": 1, "workflow_file": "workflow_resolved_v1.json"}]},
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return run_dir, workflow


def test_reproduce_comfyui_sends_sidecars_and_exits_zero(runner, monkeypatch, tmp_path):
    run_dir, workflow = _make_comfyui_run(tmp_path)
    api = FakeAPI({("POST", "/reproduce"): REPRODUCE_EXACT})
    _install(monkeypatch, api)

    result = runner.invoke(cli.aac, ["reproduce", str(run_dir)])

    assert result.exit_code == 0, result.output
    assert "exact" in result.output
    body = json.loads(api.requests[0].content)
    assert body["pipeline"] == "comfyui"
    assert body["workflows"]["1"] == workflow
    assert body["scene_py"] is None


def test_reproduce_accepts_manifest_path_directly(runner, monkeypatch, tmp_path):
    run_dir, _ = _make_comfyui_run(tmp_path)
    api = FakeAPI({("POST", "/reproduce"): REPRODUCE_EXACT})
    _install(monkeypatch, api)

    result = runner.invoke(cli.aac, ["reproduce", str(run_dir / "manifest.json")])
    assert result.exit_code == 0, result.output


def test_reproduce_blender_sends_scene_py(runner, monkeypatch, tmp_path):
    run_dir = tmp_path / "orig-run"
    run_dir.mkdir()
    (run_dir / "scene.py").write_text("import bpy\n", encoding="utf-8")
    manifest = {
        "manifest_version": 2,
        "pipeline": "blender",
        "request_id": "orig-run",
        "output_dir": str(run_dir),
        "repro": {"scene_py_sha256": "x"},
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    api = FakeAPI(
        {("POST", "/reproduce"): {**REPRODUCE_EXACT, "pipeline": "blender", "variants": []}}
    )
    _install(monkeypatch, api)

    result = runner.invoke(cli.aac, ["reproduce", str(run_dir)])

    assert result.exit_code == 0, result.output
    body = json.loads(api.requests[0].content)
    assert body["pipeline"] == "blender"
    assert body["scene_py"] == "import bpy\n"


def test_reproduce_non_reproduced_verdict_exits_one(runner, monkeypatch, tmp_path):
    run_dir, _ = _make_comfyui_run(tmp_path)
    different = {**REPRODUCE_EXACT, "verdict": "different"}
    api = FakeAPI({("POST", "/reproduce"): different})
    _install(monkeypatch, api)

    result = runner.invoke(cli.aac, ["reproduce", str(run_dir)])
    assert result.exit_code == 1
    assert "different" in result.output


def test_reproduce_rejects_manifest_without_repro_block(runner, monkeypatch, tmp_path):
    run_dir = tmp_path / "old-run"
    run_dir.mkdir()
    (run_dir / "manifest.json").write_text(
        json.dumps({"manifest_version": 1, "pipeline": "blender"}), encoding="utf-8"
    )
    api = FakeAPI({})
    _install(monkeypatch, api)

    result = runner.invoke(cli.aac, ["reproduce", str(run_dir)])

    assert result.exit_code == 1
    assert api.requests == []  # rien n'est parti vers l'API
    assert "v2" in result.output or "repro" in result.output


def test_reproduce_environment_diffs_rendered(runner, monkeypatch, tmp_path):
    run_dir, _ = _make_comfyui_run(tmp_path)
    with_diffs = {
        **REPRODUCE_EXACT,
        "verdict": "different",
        "environment_diffs": [
            {"field": "comfyui_version", "recorded": "0.20.0", "current": "0.25.0"}
        ],
    }
    api = FakeAPI({("POST", "/reproduce"): with_diffs})
    _install(monkeypatch, api)

    result = runner.invoke(cli.aac, ["reproduce", str(run_dir)])
    assert "comfyui_version : 0.20.0 → 0.25.0" in result.output


# ---------------------------------------------------------------------------
# aac resume
# ---------------------------------------------------------------------------

def test_resume_happy_path(runner, monkeypatch):
    resumed = {**EXECUTE_OK, "request_id": "req-42"}
    api = FakeAPI({("POST", "/resume"): resumed})
    _install(monkeypatch, api)

    result = runner.invoke(cli.aac, ["resume", "req-42"])

    assert result.exit_code == 0, result.output
    assert "req-42" in result.output
    body = json.loads(api.requests[0].content)
    assert body == {"request_id": "req-42"}


def test_resume_failed_run_exits_one(runner, monkeypatch):
    degraded = {
        **EXECUTE_OK,
        "execution_summary": {**EXECUTE_OK["execution_summary"],
                              "status": "degraded"},
    }
    api = FakeAPI({("POST", "/resume"): degraded})
    _install(monkeypatch, api)

    result = runner.invoke(cli.aac, ["resume", "req-42"])
    assert result.exit_code == 1


def test_resume_unknown_run_is_api_error(runner, monkeypatch):
    api = FakeAPI({("POST", "/resume"): 404})
    _install(monkeypatch, api)

    result = runner.invoke(cli.aac, ["resume", "nope"])
    assert result.exit_code == 1
