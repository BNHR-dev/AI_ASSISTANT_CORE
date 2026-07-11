"""
Tests Console — timeline events, provenance, rejeu depuis l'UI (chantier 5 v1).

Invariants couverts :
- _timeline_view : lecture tolérante d'events.jsonl, barres de durée
  proportionnelles au step le plus long, erreurs signalées, durée totale.
- _provenance_view : badges lisibles (seed, versions, commit court),
  hashes raccourcis (le hex complet reste copiable), manifest v1 → None.
- _latest_reproduce_verdict : retrouve le dernier rapport de rejeu dans
  les deux layouts (repro/<id>/<stamp>/ ComfyUI, repro-<stamp>/ Blender).
- POST /console/reproduce : même garde de chemins que /artifact (aucune
  lecture hors racines servies), run sans bloc repro → message clair,
  happy path → fragment de verdict, moteur appelé avec le bon matériel.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import console


# ---------------------------------------------------------------------------
# Timeline
# ---------------------------------------------------------------------------

def _write_events(base: Path, request_id: str, events: list[dict]) -> None:
    run_dir = base / request_id
    run_dir.mkdir(parents=True)
    (run_dir / "events.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8"
    )


_EVENTS = [
    {"ts": "2026-07-11T06:17:29.726000+00:00", "kind": "run.started",
     "data": {"message": "x", "mode": "auto"}},
    {"ts": "2026-07-11T06:17:29.727000+00:00", "kind": "route.decided",
     "data": {"task_type": "explain_basic", "selected_model": "qwen3:8b"}},
    {"ts": "2026-07-11T06:17:29.727100+00:00", "kind": "plan.built",
     "data": {"strategy": "single", "steps": [{"step_id": "step_primary"}]}},
    {"ts": "2026-07-11T06:17:29.728000+00:00", "kind": "step.started",
     "data": {"step_id": "step_primary", "step_type": "agent"}},
    {"ts": "2026-07-11T06:18:12.663000+00:00", "kind": "step.finished",
     "data": {"step_id": "step_primary", "status": "success", "duration_ms": 42936}},
    {"ts": "2026-07-11T06:18:12.664000+00:00", "kind": "run.finished",
     "data": {"execution_summary": {"status": "success"}, "duration_ms": 42937}},
]


def test_timeline_view_rows_and_total(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AAC_RUN_EVENTS_DIR", str(tmp_path))
    _write_events(tmp_path, "run-1", _EVENTS)

    view = console._timeline_view("run-1")

    assert view is not None
    assert view["total_ms"] == 42937
    kinds = [r["kind"] for r in view["rows"]]
    assert kinds == ["run.started", "route.decided", "plan.built",
                     "step.started", "step.finished", "run.finished"]
    decided = view["rows"][1]
    assert decided["family"] == "route"
    assert "explain_basic" in decided["label"] and "qwen3:8b" in decided["label"]
    finished = view["rows"][4]
    assert finished["duration_ms"] == 42936
    assert finished["bar_pct"] == 100  # step le plus long = pleine barre
    assert finished["error"] is False


def test_timeline_view_flags_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AAC_RUN_EVENTS_DIR", str(tmp_path))
    events = [
        {"ts": "2026-07-11T06:00:00.000000+00:00", "kind": "step.finished",
         "data": {"step_id": "s1", "status": "error", "duration_ms": 10,
                  "error": "ollama down"}},
        {"ts": "2026-07-11T06:00:00.100000+00:00", "kind": "run.finished",
         "data": {"execution_summary": {"status": "failed"}, "duration_ms": 12}},
    ]
    _write_events(tmp_path, "run-err", events)

    view = console._timeline_view("run-err")

    assert view["rows"][0]["error"] is True
    assert view["rows"][0]["error_text"] == "ollama down"
    assert view["rows"][1]["error"] is True


def test_timeline_view_none_without_journal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AAC_RUN_EVENTS_DIR", str(tmp_path))
    assert console._timeline_view("no-such-run") is None


def test_timeline_view_tolerates_corrupt_lines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AAC_RUN_EVENTS_DIR", str(tmp_path))
    run_dir = tmp_path / "run-bad"
    run_dir.mkdir()
    (run_dir / "events.jsonl").write_text(
        '{"ts": "t", "kind": "run.started", "data": {}}\nNOT JSON\n[1,2]\n',
        encoding="utf-8",
    )
    view = console._timeline_view("run-bad")
    assert view is not None and len(view["rows"]) == 1


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------

_MANIFEST_V2 = {
    "manifest_version": 2,
    "pipeline": "comfyui",
    "request_id": "run-1",
    "repro": {
        "repro_version": 2,
        "aac_git_commit": "57df8b1234567890abcdef",
        "comfyui": {"comfyui_version": "0.25.0", "pytorch_version": "2.11.0+cu128"},
        "models": {"checkpoints": [{"name": "RealVisXL.safetensors", "sha256": "c" * 64}],
                   "upscale_models": []},
        "variants": [{
            "index": 1, "seed": 3857181658, "workflow_sha256": "a" * 64,
            "workflow_file": "workflow_resolved_v1.json",
            "image": {"filename": "img.png", "sha256": "b" * 64,
                      "pixels_sha256": "d" * 64, "dhash": "e3c9b069495bc68e"},
        }],
    },
}


def test_provenance_badges_and_short_hashes(tmp_path: Path) -> None:
    view = console._provenance_view(_MANIFEST_V2, tmp_path, "run-1")

    assert view is not None
    labels = {b["label"]: b["value"] for b in view["badges"]}
    assert labels["seed"] == "3857181658"
    assert labels["engine"] == "ComfyUI 0.25.0"
    assert labels["torch"] == "2.11.0+cu128"
    assert labels["commit"] == "57df8b123"
    by_label = {h["label"]: h for h in view["hashes"]}
    wf = by_label["workflow v1"]
    assert wf["full"] == "a" * 64
    assert "…" in wf["short"] and len(wf["short"]) < 30
    assert "checkpoints/RealVisXL.safetensors" in by_label


def test_provenance_none_for_v1_manifest(tmp_path: Path) -> None:
    assert console._provenance_view({"manifest_version": 1}, tmp_path, "x") is None
    assert console._provenance_view(None, tmp_path, "x") is None


def test_latest_reproduce_verdict_both_layouts(tmp_path: Path) -> None:
    run_dir = tmp_path / "run-1"
    run_dir.mkdir()
    # Layout ComfyUI : <runs>/repro/<run_id>/<stamp>/reproduce_report.json
    older = tmp_path / "repro" / "run-1" / "aaa"
    older.mkdir(parents=True)
    (older / "reproduce_report.json").write_text(json.dumps(
        {"reproduced_request_id": "run-1", "verdict": "different",
         "created_at": "2026-07-11T05:00:00+00:00"}), encoding="utf-8")
    # Layout Blender : <runs>/repro-<stamp>/ (matché par reproduced_request_id)
    newer = tmp_path / "repro-bbb"
    newer.mkdir()
    (newer / "reproduce_report.json").write_text(json.dumps(
        {"reproduced_request_id": "run-1", "verdict": "exact",
         "created_at": "2026-07-11T08:00:00+00:00"}), encoding="utf-8")
    # Rapport d'un AUTRE run : ignoré.
    foreign = tmp_path / "repro-ccc"
    foreign.mkdir()
    (foreign / "reproduce_report.json").write_text(json.dumps(
        {"reproduced_request_id": "other", "verdict": "failed",
         "created_at": "2026-07-11T09:00:00+00:00"}), encoding="utf-8")

    verdict = console._latest_reproduce_verdict(run_dir, "run-1")

    assert verdict == {"verdict": "exact", "created_at": "2026-07-11 08:00"}


# ---------------------------------------------------------------------------
# POST /console/reproduce
# ---------------------------------------------------------------------------

@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    # Racines servies confinées au tmp du test (même garde que la prod).
    monkeypatch.setattr(console, "_SERVE_ROOTS", [tmp_path.resolve()])
    app = FastAPI()
    app.include_router(console.router)
    return TestClient(app)


def _make_run(tmp_path: Path) -> Path:
    run_dir = tmp_path / "run-1"
    run_dir.mkdir()
    (run_dir / "manifest.json").write_text(json.dumps(_MANIFEST_V2), encoding="utf-8")
    (run_dir / "workflow_resolved_v1.json").write_text(
        json.dumps({"3": {"inputs": {"seed": 3857181658}}}), encoding="utf-8"
    )
    return run_dir


def test_reproduce_route_rejects_paths_outside_roots(client: TestClient) -> None:
    assert client.post("/console/reproduce", params={"path": "/etc"}).status_code == 404


def test_reproduce_route_explains_missing_repro_block(
    client: TestClient, tmp_path: Path
) -> None:
    run_dir = tmp_path / "old-run"
    run_dir.mkdir()
    (run_dir / "manifest.json").write_text(
        json.dumps({"manifest_version": 1, "pipeline": "blender"}), encoding="utf-8"
    )
    response = client.post("/console/reproduce", params={"path": str(run_dir)})
    assert response.status_code == 200
    assert "no repro block" in response.text


def test_reproduce_route_happy_path_calls_engine(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = _make_run(tmp_path)
    captured: dict = {}

    def fake_reproduce_run(pipeline, manifest, *, workflows=None, scene_py=None):
        captured.update(pipeline=pipeline, workflows=workflows, scene_py=scene_py)
        return {
            "pipeline": pipeline, "verdict": "exact", "dhash_threshold": 4,
            "duration_ms": 42000, "environment_diffs": [],
            "variants": [{"index": 1, "verdict": "exact",
                          "image": {"dhash_distance": 0}}],
            "report_path": "/outputs/x/reproduce_report.json",
        }

    monkeypatch.setattr(console, "reproduce_run", fake_reproduce_run)
    response = client.post("/console/reproduce", params={"path": str(run_dir)})

    assert response.status_code == 200
    assert "pixel-identical" in response.text
    assert "verdict-exact" in response.text
    assert captured["pipeline"] == "comfyui"
    assert captured["workflows"] == {1: {"3": {"inputs": {"seed": 3857181658}}}}


def test_reproduce_route_engine_failure_is_rendered_not_raised(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = _make_run(tmp_path)

    def boom(*args, **kwargs):
        raise RuntimeError("comfyui down")

    monkeypatch.setattr(console, "reproduce_run", boom)
    response = client.post("/console/reproduce", params={"path": str(run_dir)})

    assert response.status_code == 200
    assert "comfyui down" in response.text


def test_run_detail_includes_new_sections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AAC_RUN_EVENTS_DIR", str(tmp_path / "events"))
    _write_events(tmp_path / "events", "run-1", _EVENTS)
    run_dir = _make_run(tmp_path)

    detail = console.build_run_detail(run_dir)

    assert detail["can_reproduce"] is True
    assert detail["timeline"] is not None and len(detail["timeline"]["rows"]) == 6
    assert detail["provenance"] is not None
    assert any(b["label"] == "seed" for b in detail["provenance"]["badges"])
