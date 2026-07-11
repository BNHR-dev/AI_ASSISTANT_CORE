"""
Tests du bloc repro du manifest ComfyUI (v2) et des sidecars workflow.

Invariants couverts :
- Le manifest v2 expose repro.{aac_git_commit, comfyui, models, variants}.
- Les noms de modèles viennent du workflow RÉSOLU (ce qui a été envoyé),
  pas de l'env ; sha best-effort via COMFYUI_MODELS_DIR (null sinon).
- Chaque variante porte son seed effectif, le hash canonique du workflow,
  et les hashes tier 3 (sha256 + dHash) de son image.
- write_comfyui_manifest écrit un sidecar workflow_resolved_v<i>.json par
  variante (le fichier que `aac reproduce` rejouera) et le référence.
- Un résultat sans runs (mocks, anciens appelants) donne un bloc repro
  vide mais présent — jamais d'échec.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.engine import repro
from app.engine.comfyui_manifest import (
    MANIFEST_VERSION,
    WORKFLOW_SIDECAR_TEMPLATE,
    build_comfyui_manifest,
    write_comfyui_manifest,
)


def _fake_workflow(seed: int) -> dict:
    return {
        "3": {"class_type": "KSampler", "inputs": {"seed": seed, "steps": 30, "cfg": 7.0}},
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "RealVisXL_V5.0_fp16.safetensors"}},
        "9": {"class_type": "UpscaleModelLoader", "inputs": {"model_name": "4x-UltraSharp.pth"}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "a watch on a backdrop"}},
    }


def _write_png(path: Path) -> None:
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (16, 16), (200, 30, 30)).save(path)


def _fake_result(tmp_path: Path, *, with_image: bool = True) -> dict:
    image_path = tmp_path / "run" / "img_00001_.png"
    if with_image:
        _write_png(image_path)
    run = {
        "prompt_id": "prompt-123",
        "filename": image_path.name if with_image else None,
        "output_path": str(image_path) if with_image else None,
        "seed": 1834027,
        "workflow_resolved": _fake_workflow(1834027),
    }
    return {
        "status": "success",
        "workflow_id": "object_basic_v1",
        "filename": run["filename"],
        "output_path": run["output_path"],
        "parameters": {"positive_prompt": "a watch", "seed": 1834027, "quality": "draft"},
        "raw_response": {"runs": [run]},
        "variants_count": 1,
        "completed_variants": 1 if with_image else 0,
        "partial": False,
    }


_TIMING = {"started_at": "t0", "finished_at": "t1", "duration_ms": 42}


def _build(result: dict, output_dir: str, **kwargs) -> dict:
    return build_comfyui_manifest(
        "req-1", result, timing=_TIMING, route=[], output_dir=output_dir, **kwargs
    )


# ---------------------------------------------------------------------------
# build : bloc repro
# ---------------------------------------------------------------------------

def test_manifest_v2_has_repro_block(tmp_path: Path) -> None:
    manifest = _build(_fake_result(tmp_path), str(tmp_path / "run"))
    assert manifest["manifest_version"] == MANIFEST_VERSION == 2
    repro_block = manifest["repro"]
    assert repro_block["repro_version"] == repro.REPRO_VERSION
    assert set(repro_block) == {"repro_version", "aac_git_commit", "comfyui", "models", "variants"}


def test_variant_entry_seed_workflow_hash_and_image(tmp_path: Path) -> None:
    result = _fake_result(tmp_path)
    manifest = _build(result, str(tmp_path / "run"))
    (variant,) = manifest["repro"]["variants"]

    assert variant["index"] == 1
    assert variant["seed"] == 1834027
    assert variant["prompt_id"] == "prompt-123"
    assert variant["workflow_sha256"] == repro.sha256_canonical_json(_fake_workflow(1834027))
    assert variant["workflow_file"] is None  # renseigné par write_ seulement

    image = variant["image"]
    assert image["filename"] == "img_00001_.png"
    assert image["sha256"] == repro.sha256_file(result["output_path"])
    assert image["pixels_sha256"] == repro.sha256_image_pixels(result["output_path"])
    assert image["dhash"] is not None and len(image["dhash"]) == 16


def test_variant_without_image_has_null_image(tmp_path: Path) -> None:
    manifest = _build(_fake_result(tmp_path, with_image=False), str(tmp_path / "run"))
    (variant,) = manifest["repro"]["variants"]
    assert variant["image"] is None
    assert variant["workflow_sha256"] is not None  # le workflow envoyé reste capturé


def test_model_names_extracted_from_resolved_workflow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(repro.COMFYUI_MODELS_DIR_ENV, raising=False)
    manifest = _build(_fake_result(tmp_path), str(tmp_path / "run"))
    models = manifest["repro"]["models"]
    assert models["checkpoints"] == [{"name": "RealVisXL_V5.0_fp16.safetensors", "sha256": None}]
    assert models["upscale_models"] == [{"name": "4x-UltraSharp.pth", "sha256": None}]


def test_model_sha_filled_when_models_dir_readable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    models_dir = tmp_path / "models"
    ckpt = models_dir / "checkpoints" / "RealVisXL_V5.0_fp16.safetensors"
    ckpt.parent.mkdir(parents=True)
    ckpt.write_bytes(b"fake weights")
    monkeypatch.setenv(repro.COMFYUI_MODELS_DIR_ENV, str(models_dir))

    manifest = _build(_fake_result(tmp_path), str(tmp_path / "run"))
    (entry,) = manifest["repro"]["models"]["checkpoints"]
    assert entry["sha256"] == repro.sha256_file(ckpt)


def test_result_without_runs_yields_empty_repro(tmp_path: Path) -> None:
    result = _fake_result(tmp_path)
    result["raw_response"] = None  # ancien appelant / mock minimal
    manifest = _build(result, str(tmp_path / "run"))
    assert manifest["repro"]["variants"] == []
    assert manifest["repro"]["models"] == {"checkpoints": [], "upscale_models": []}


def test_system_info_passed_through(tmp_path: Path) -> None:
    info = {"comfyui_version": "0.3.x", "pytorch_version": "2.11.0", "python_version": "3.14"}
    manifest = _build(_fake_result(tmp_path), str(tmp_path / "run"), comfyui_system_info=info)
    assert manifest["repro"]["comfyui"] == info


# ---------------------------------------------------------------------------
# write : sidecars workflow
# ---------------------------------------------------------------------------

def test_write_creates_workflow_sidecar_and_references_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Pas de sonde HTTP réelle dans les tests.
    monkeypatch.setattr(
        "app.clients.comfyui_client.get_comfyui_system_info",
        lambda: {"comfyui_version": "test"},
    )
    result = _fake_result(tmp_path)
    output_dir = str(tmp_path / "run")

    manifest_path = write_comfyui_manifest(
        "req-1", result, output_dir=output_dir, timing=_TIMING, route=[]
    )

    assert manifest_path is not None
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))

    sidecar_name = WORKFLOW_SIDECAR_TEMPLATE.format(index=1)
    (variant,) = manifest["repro"]["variants"]
    assert variant["workflow_file"] == sidecar_name
    assert manifest["repro"]["comfyui"] == {"comfyui_version": "test"}

    sidecar = json.loads((Path(output_dir) / sidecar_name).read_text(encoding="utf-8"))
    assert sidecar == _fake_workflow(1834027)
    # Le sidecar re-hashé correspond au hash enregistré : rejouable et vérifiable.
    assert repro.sha256_canonical_json(sidecar) == variant["workflow_sha256"]


def test_write_survives_probe_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom():
        raise RuntimeError("comfyui down")

    monkeypatch.setattr("app.clients.comfyui_client.get_comfyui_system_info", boom)
    manifest_path = write_comfyui_manifest(
        "req-1", _fake_result(tmp_path), output_dir=str(tmp_path / "run"),
        timing=_TIMING, route=[],
    )
    assert manifest_path is not None
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    assert manifest["repro"]["comfyui"] is None
