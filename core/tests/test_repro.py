"""
Tests des utilitaires de reproductibilité (app.engine.repro).

Invariants couverts :
- Hash canonique : invariance à l'ordre des clés, arrondi des floats,
  normalisation -0.0, refus du non-sérialisable.
- Hash sémantique du scene_report : les clés `*_path` sont exclues
  (chemins volatils), la sémantique de scène fait foi.
- dHash : déterministe, stable au re-encodage, distant entre images
  différentes, robuste aux fichiers invalides.
- Sondes env (git commit, version Blender) : best-effort, mémoïsées,
  overridables par env.
- Hash des fichiers modèles : cache sidecar validé par (taille, mtime),
  kill-switch AAC_REPRO_HASH_MODELS qui laisse le cache lisible.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from app.engine import repro


@pytest.fixture(autouse=True)
def _fresh_probe_memos():
    repro.reset_probe_memos()
    yield
    repro.reset_probe_memos()


# ---------------------------------------------------------------------------
# sha256_file
# ---------------------------------------------------------------------------

def test_sha256_file_known_value(tmp_path: Path) -> None:
    f = tmp_path / "x.txt"
    f.write_bytes(b"aac")
    assert repro.sha256_file(f) == hashlib.sha256(b"aac").hexdigest()


def test_sha256_file_missing_or_none(tmp_path: Path) -> None:
    assert repro.sha256_file(tmp_path / "absent.bin") is None
    assert repro.sha256_file(None) is None


# ---------------------------------------------------------------------------
# sha256_canonical_json
# ---------------------------------------------------------------------------

def test_canonical_json_key_order_invariant() -> None:
    a = {"b": 1, "a": [{"y": 2, "x": 3}]}
    b = {"a": [{"x": 3, "y": 2}], "b": 1}
    assert repro.sha256_canonical_json(a) == repro.sha256_canonical_json(b)


def test_canonical_json_float_rounding() -> None:
    # 0.1 + 0.2 != 0.3 en flottant ; la canonicalisation absorbe ce bruit.
    assert repro.sha256_canonical_json({"v": 0.1 + 0.2}) == repro.sha256_canonical_json(
        {"v": 0.3}
    )
    assert repro.sha256_canonical_json({"v": -0.0}) == repro.sha256_canonical_json({"v": 0.0})


def test_canonical_json_real_change_changes_hash() -> None:
    assert repro.sha256_canonical_json({"v": 0.30001}) != repro.sha256_canonical_json(
        {"v": 0.30002}
    )


def test_canonical_json_unserializable_returns_none() -> None:
    assert repro.sha256_canonical_json({"v": object()}) is None


# ---------------------------------------------------------------------------
# semantic_scene_report_hash
# ---------------------------------------------------------------------------

_REPORT = {
    "scene_report_path": "/outputs/blender/run-a/scene_report.json",
    "template_name": "product_render",
    "object_count": 6,
    "object_names": ["Backdrop_Plane", "Hero"],
    "violations": [],
    "status": "passed",
    "nested": {"preview_path": "/outputs/blender/run-a/preview.png", "kept": 1},
}


def test_semantic_hash_ignores_paths() -> None:
    other = json.loads(json.dumps(_REPORT))
    other["scene_report_path"] = "/somewhere/else/scene_report.json"
    other["nested"]["preview_path"] = "C:/windows/style/preview.png"
    assert repro.semantic_scene_report_hash(_REPORT) == repro.semantic_scene_report_hash(other)


def test_semantic_hash_detects_scene_change() -> None:
    other = json.loads(json.dumps(_REPORT))
    other["object_names"] = ["Backdrop_Plane", "Intruder"]
    assert repro.semantic_scene_report_hash(_REPORT) != repro.semantic_scene_report_hash(other)


def test_semantic_hash_ignores_execution_noise() -> None:
    # Mesuré live 2026-07-11 : deux exécutions du même scene.py produisent la
    # même scène mais des mtimes, tailles de preview et métriques pixel de
    # visual-QA différents. Le tier 2 doit y être INSENSIBLE.
    base = {
        **_REPORT,
        "framing_contract": {
            "status": "passed", "violations": [], "screen_bbox": [0.42, 0.23, 0.52, 0.62],
            "occupancy": 0.39,
            "framing_divergence": {"iou": 0.0966, "perceptual_bbox_fraction": [0.39, 0.43, 1.0, 1.0]},
        },
        "runtime_contract": {
            "status": "passed", "corrections_applied": ["normalize_lighting"],
            "after": {"preview_mtime_iso": "2026-07-11T06:55:15+00:00", "preview_size_bytes": 165059},
        },
        "visual_qa": {"status": "degraded", "violations": ["subject_offcenter"]},
        "ast_guard": {"status": "passed", "metrics": {"raw_code_length": 4008}},
    }
    noisy = json.loads(json.dumps(base))
    noisy["framing_contract"]["framing_divergence"] = {"iou": 0.0625, "perceptual_bbox_fraction": [0.22, 0.09, 0.93, 1.0]}
    noisy["runtime_contract"]["after"] = {"preview_mtime_iso": "2026-07-11T07:22:09+00:00", "preview_size_bytes": 162487}
    noisy["visual_qa"] = {"status": "passed", "violations": []}
    noisy["ast_guard"] = {"status": "skipped", "metrics": {}}

    assert repro.semantic_scene_report_hash(base) == repro.semantic_scene_report_hash(noisy)

    # Mais un changement GÉOMÉTRIQUE du cadrage reste détecté.
    moved = json.loads(json.dumps(base))
    moved["framing_contract"]["occupancy"] = 0.17
    assert repro.semantic_scene_report_hash(base) != repro.semantic_scene_report_hash(moved)


@pytest.mark.parametrize("bad", [None, {}, [], "x", 42])
def test_semantic_hash_rejects_non_report(bad) -> None:
    assert repro.semantic_scene_report_hash(bad) is None


# ---------------------------------------------------------------------------
# dHash
# ---------------------------------------------------------------------------

def _write_gradient(path: Path, *, flip: bool = False, size: int = 64) -> None:
    from PIL import Image

    img = Image.new("L", (size, size))
    img.putdata(
        [
            (size - 1 - x if flip else x) * 255 // (size - 1)
            for _y in range(size)
            for x in range(size)
        ]
    )
    img.save(path)


def test_dhash_deterministic_and_hex(tmp_path: Path) -> None:
    a = tmp_path / "a.png"
    _write_gradient(a)
    h1 = repro.dhash_image(a)
    h2 = repro.dhash_image(a)
    assert h1 == h2
    assert h1 is not None and len(h1) == 16
    int(h1, 16)  # hex valide


def test_dhash_differs_on_different_image(tmp_path: Path) -> None:
    a, b = tmp_path / "a.png", tmp_path / "b.png"
    _write_gradient(a)
    _write_gradient(b, flip=True)
    distance = repro.dhash_distance(repro.dhash_image(a), repro.dhash_image(b))
    assert distance is not None and distance > 16  # gradient inversé = très loin


def test_dhash_invalid_inputs(tmp_path: Path) -> None:
    corrupt = tmp_path / "corrupt.png"
    corrupt.write_bytes(b"not an image")
    assert repro.dhash_image(corrupt) is None
    assert repro.dhash_image(tmp_path / "absent.png") is None
    assert repro.dhash_image(None) is None


def test_pixels_sha256_ignores_metadata(tmp_path: Path) -> None:
    # Même pixels, métadonnées PNG différentes (cas ComfyUI : prompt embarqué)
    # → sha256 fichier divergent, pixels_sha256 identique.
    from PIL import Image
    from PIL.PngImagePlugin import PngInfo

    a, b = tmp_path / "a.png", tmp_path / "b.png"
    img = Image.new("RGB", (8, 8), (10, 20, 30))
    img.save(a)
    meta = PngInfo()
    meta.add_text("prompt", '{"seed": 42}')
    img.save(b, pnginfo=meta)

    assert repro.sha256_file(a) != repro.sha256_file(b)
    assert repro.sha256_image_pixels(a) == repro.sha256_image_pixels(b)
    assert repro.sha256_image_pixels(a) is not None


def test_pixels_sha256_invalid_inputs(tmp_path: Path) -> None:
    corrupt = tmp_path / "x.png"
    corrupt.write_bytes(b"nope")
    assert repro.sha256_image_pixels(corrupt) is None
    assert repro.sha256_image_pixels(None) is None


def test_dhash_distance_contract() -> None:
    assert repro.dhash_distance("00ff", "00ff") == 0
    assert repro.dhash_distance("00ff", "00fe") == 1
    assert repro.dhash_distance(None, "00ff") is None
    assert repro.dhash_distance("00ff", "00ffaa") is None
    assert repro.dhash_distance("zz", "zz") is None  # hex invalide


# ---------------------------------------------------------------------------
# aac_git_commit
# ---------------------------------------------------------------------------

def test_git_commit_env_stamp_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(repro.AAC_GIT_COMMIT_ENV, " abc123 ")
    assert repro.aac_git_commit() == "abc123"


def test_git_commit_memoized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(repro.AAC_GIT_COMMIT_ENV, "first")
    assert repro.aac_git_commit() == "first"
    monkeypatch.setenv(repro.AAC_GIT_COMMIT_ENV, "second")
    assert repro.aac_git_commit() == "first"  # mémo : sondé une fois par process


def test_git_commit_from_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    # Sans stamp env, dans ce repo : un vrai hash de commit (40 hex).
    monkeypatch.delenv(repro.AAC_GIT_COMMIT_ENV, raising=False)
    commit = repro.aac_git_commit()
    assert commit is not None and len(commit) == 40
    int(commit, 16)


def test_git_commit_none_when_git_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(repro.AAC_GIT_COMMIT_ENV, raising=False)

    def boom(*args, **kwargs):
        raise OSError("no git")

    monkeypatch.setattr(subprocess, "run", boom)
    assert repro.aac_git_commit() is None


# ---------------------------------------------------------------------------
# blender_version
# ---------------------------------------------------------------------------

def test_blender_version_probes_first_line(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BLENDER_EXE", "blender-fake")
    monkeypatch.setattr("app.engine.repro.shutil.which", lambda exe: "/usr/bin/blender-fake")

    class FakeProc:
        returncode = 0
        stdout = "Blender 9.9.9\n\tbuild date: 2099-01-01\n"

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeProc())
    assert repro.blender_version() == "Blender 9.9.9"


def test_blender_version_none_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BLENDER_EXE", "/nonexistent/blender")
    monkeypatch.setattr("app.engine.repro.shutil.which", lambda exe: None)
    assert repro.blender_version() is None


def test_blender_version_memoized_per_exe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BLENDER_EXE", "blender-fake")
    monkeypatch.setattr("app.engine.repro.shutil.which", lambda exe: "/usr/bin/blender-fake")
    calls = {"n": 0}

    class FakeProc:
        returncode = 0
        stdout = "Blender 9.9.9\n"

    def counting_run(*a, **k):
        calls["n"] += 1
        return FakeProc()

    monkeypatch.setattr(subprocess, "run", counting_run)
    repro.blender_version()
    repro.blender_version()
    assert calls["n"] == 1


# ---------------------------------------------------------------------------
# model_file_sha256 + comfyui_models_dir
# ---------------------------------------------------------------------------

def test_model_sha_computes_and_caches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(repro.REPRO_HASH_MODELS_ENV, raising=False)
    model = tmp_path / "model.safetensors"
    model.write_bytes(b"weights")
    expected = hashlib.sha256(b"weights").hexdigest()

    assert repro.model_file_sha256(model) == expected
    sidecar = tmp_path / "model.safetensors.sha256.json"
    assert sidecar.exists()

    # Deuxième appel : servi par le cache (on empoisonne le contenu du fichier
    # SANS toucher taille/mtime — un recalcul donnerait un autre hash).
    stat = model.stat()
    model.write_bytes(b"poison!")  # même taille (7 octets)
    import os

    os.utime(model, (stat.st_atime, stat.st_mtime))
    assert repro.model_file_sha256(model) == expected


def test_model_sha_cache_invalidated_on_change(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(repro.REPRO_HASH_MODELS_ENV, raising=False)
    model = tmp_path / "model.safetensors"
    model.write_bytes(b"v1")
    repro.model_file_sha256(model)
    model.write_bytes(b"v2-longer")  # taille différente → cache invalide
    assert repro.model_file_sha256(model) == hashlib.sha256(b"v2-longer").hexdigest()


def test_model_sha_disabled_still_reads_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    model = tmp_path / "model.safetensors"
    model.write_bytes(b"weights")
    monkeypatch.delenv(repro.REPRO_HASH_MODELS_ENV, raising=False)
    expected = repro.model_file_sha256(model)  # calcule + écrit le cache

    monkeypatch.setenv(repro.REPRO_HASH_MODELS_ENV, "0")
    assert repro.model_file_sha256(model) == expected  # cache valide → lu

    fresh = tmp_path / "fresh.safetensors"
    fresh.write_bytes(b"no cache")
    assert repro.model_file_sha256(fresh) is None  # pas de cache → pas de calcul


def test_model_sha_missing_file() -> None:
    assert repro.model_file_sha256("/nonexistent/model.safetensors") is None
    assert repro.model_file_sha256(None) is None


def test_comfyui_models_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(repro.COMFYUI_MODELS_DIR_ENV, raising=False)
    assert repro.comfyui_models_dir() is None
    monkeypatch.setenv(repro.COMFYUI_MODELS_DIR_ENV, str(tmp_path / "nope"))
    assert repro.comfyui_models_dir() is None
    monkeypatch.setenv(repro.COMFYUI_MODELS_DIR_ENV, str(tmp_path))
    assert repro.comfyui_models_dir() == tmp_path
