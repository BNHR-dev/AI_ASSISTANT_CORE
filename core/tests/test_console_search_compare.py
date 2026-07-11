"""
Tests recherche + comparaison de la Console (chantier 5 v2b).

Invariants couverts :
- filter_runs : tous les tokens exigés (ET), insensible casse/accents
  (« theiere » ⇔ « théière »), préfixe (frappe en cours), requête vide =
  tout, matche aussi le request_id, ordre préservé.
- GET /console/outputs?q= : filtre + compteur x/total + message « no run
  matches » distinct du « no run yet » ; les rejeux (repro*) sont exclus
  de la liste.
- build_compare_view : lignes alignées, `changed` seulement quand les
  DEUX valeurs existent et diffèrent, distance dHash entre les images.
- GET /console/compare : exactement 2 sélections exigées (message sinon),
  garde de chemins (hors racines → 404), rendu avec diff surligné.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import console


def _make_run(base: Path, run_id: str, *, prompt: str, seed: int,
              dhash: str = "e3c9b069495bc68e", pixels: str = "a" * 64) -> Path:
    d = base / run_id
    d.mkdir(parents=True)
    (d / "manifest.json").write_text(json.dumps({
        "manifest_version": 2,
        "pipeline": "comfyui",
        "request_id": run_id,
        "status": "success",
        "input": {"prompt": prompt},
        "repro": {
            "repro_version": 2,
            "aac_git_commit": "e5c3b04aaaaaaaaaaaaa",
            "comfyui": {"comfyui_version": "0.25.0", "pytorch_version": "2.11.0+cu128"},
            "models": {"checkpoints": [{"name": "RealVisXL.safetensors", "sha256": "c" * 64}],
                       "upscale_models": []},
            "variants": [{"index": 1, "seed": seed, "workflow_sha256": "b" * 64,
                          "workflow_file": "workflow_resolved_v1.json",
                          "image": {"filename": "x.png", "sha256": "d" * 64,
                                    "pixels_sha256": pixels, "dhash": dhash}}],
        },
    }), encoding="utf-8")
    return d


@pytest.fixture
def outputs_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    comfy = tmp_path / "comfyui"
    blender = tmp_path / "blender"
    comfy.mkdir()
    blender.mkdir()
    monkeypatch.setattr(console, "COMFYUI_RUNS_DIR", comfy)
    monkeypatch.setattr(console, "BLENDER_RUNS_DIR", blender)
    monkeypatch.setattr(console, "_SERVE_ROOTS", [tmp_path.resolve()])
    return comfy


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(console.router)
    return TestClient(app)


# ---------------------------------------------------------------------------
# filter_runs
# ---------------------------------------------------------------------------

_RUNS = [
    {"id": "run-1", "prompt": "une montre de luxe sur fond sombre", "mtime": 3},
    {"id": "run-2", "prompt": "une théière en cuivre, éclairage studio", "mtime": 2},
    {"id": "run-3", "prompt": None, "mtime": 1},
]


def test_filter_all_tokens_required() -> None:
    assert [r["id"] for r in console.filter_runs("montre sombre", _RUNS)] == ["run-1"]
    assert console.filter_runs("montre cuivre", _RUNS) == []


def test_filter_accent_and_case_insensitive() -> None:
    assert [r["id"] for r in console.filter_runs("THEIERE", _RUNS)] == ["run-2"]
    assert [r["id"] for r in console.filter_runs("théière", _RUNS)] == ["run-2"]


def test_filter_prefix_matches_while_typing() -> None:
    assert [r["id"] for r in console.filter_runs("mont", _RUNS)] == ["run-1"]


def test_filter_empty_returns_everything() -> None:
    assert console.filter_runs("  ", _RUNS) == _RUNS


def test_filter_matches_run_id_too() -> None:
    assert [r["id"] for r in console.filter_runs("run-3", _RUNS)] == ["run-3"]


# ---------------------------------------------------------------------------
# GET /console/outputs?q=
# ---------------------------------------------------------------------------

def test_outputs_search_filters_and_counts(client, outputs_env: Path) -> None:
    _make_run(outputs_env, "run-a", prompt="une montre de luxe", seed=1)
    _make_run(outputs_env, "run-b", prompt="un sablier en verre", seed=2)

    everything = client.get("/console/outputs").text
    assert "run-a" in everything and "run-b" in everything
    assert "une montre de luxe" in everything  # prompt visible sur la carte

    filtered = client.get("/console/outputs", params={"q": "montre"}).text
    assert "run-a" in filtered and "run-b" not in filtered
    assert "1 / 2 run(s)" in filtered

    nothing = client.get("/console/outputs", params={"q": "zeppelin"}).text
    assert "No run matches" in nothing and "2 run(s) on disk" in nothing


def test_outputs_excludes_repro_replays(client, outputs_env: Path) -> None:
    _make_run(outputs_env, "run-a", prompt="une montre", seed=1)
    (outputs_env / "repro").mkdir()          # rejeux ComfyUI
    _make_run(outputs_env.parent / "blender", "repro-abc12345", prompt="rejeu", seed=9)

    body = client.get("/console/outputs").text
    assert "run-a" in body
    assert "repro-abc12345" not in body


# ---------------------------------------------------------------------------
# build_compare_view
# ---------------------------------------------------------------------------

def test_compare_view_marks_only_real_differences(outputs_env: Path) -> None:
    a_dir = _make_run(outputs_env, "run-a", prompt="montre", seed=111,
                      dhash="00ff00ff00ff00ff")
    b_dir = _make_run(outputs_env, "run-b", prompt="montre", seed=222,
                      dhash="00ff00ff00ff00fe")
    view = console.build_compare_view(
        console.build_run_detail(a_dir), console.build_run_detail(b_dir)
    )
    by_label = {row["label"]: row for row in view["rows"]}
    assert by_label["seed"]["changed"] is True
    assert by_label["engine"]["changed"] is False      # même version ComfyUI
    assert by_label["prompt"]["changed"] is False
    assert view["dhash_distance"] == 1


def test_compare_view_missing_side_is_not_a_diff(outputs_env: Path) -> None:
    a_dir = _make_run(outputs_env, "run-a", prompt="montre", seed=1)
    b_dir = outputs_env / "run-nomanifest"
    b_dir.mkdir()
    view = console.build_compare_view(
        console.build_run_detail(a_dir), console.build_run_detail(b_dir)
    )
    assert all(row["changed"] is False for row in view["rows"])
    assert view["dhash_distance"] is None


# ---------------------------------------------------------------------------
# GET /console/compare
# ---------------------------------------------------------------------------

def test_compare_requires_exactly_two(client, outputs_env: Path) -> None:
    a_dir = _make_run(outputs_env, "run-a", prompt="montre", seed=1)
    one = client.get("/console/compare", params={"sel": [str(a_dir)]})
    assert one.status_code == 200
    assert "exactly two" in one.text
    none = client.get("/console/compare")
    assert "exactly two" in none.text


def test_compare_guards_paths(client, outputs_env: Path) -> None:
    a_dir = _make_run(outputs_env, "run-a", prompt="montre", seed=1)
    response = client.get(
        "/console/compare", params={"sel": [str(a_dir), "/etc"]}
    )
    assert response.status_code == 404


def test_compare_renders_diff(client, outputs_env: Path) -> None:
    a_dir = _make_run(outputs_env, "run-a", prompt="montre", seed=111)
    b_dir = _make_run(outputs_env, "run-b", prompt="montre", seed=222)

    body = client.get(
        "/console/compare", params={"sel": [str(a_dir), str(b_dir)]}
    ).text

    assert "run-a" in body and "run-b" in body
    assert "diff-changed" in body           # au moins le seed diffère
    assert "111" in body and "222" in body
    assert "Visual distance" in body
