"""
Tests unitaires — artifact_manifest.py (Phase H.1).
Vérifie build_blender_manifest et write_blender_manifest sans Blender réel.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch


from app.engine.artifact_manifest import (
    MANIFEST_VERSION,
    PIPELINE,
    build_blender_manifest,
    write_blender_manifest,
)
from app.engine.blender_types import BlenderRequest, BlenderResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_FAKE_ID = "test-manifest-001"
_FAKE_DIR = f"outputs/blender/{_FAKE_ID}"
_FAKE_BLEND = f"{_FAKE_DIR}/scene.blend"
_FAKE_SCRIPT = f"{_FAKE_DIR}/scene.py"
_FAKE_RENDER = f"{_FAKE_DIR}/preview.png"


def _make_request(source_prompt: str | None = "une sphère bleue") -> BlenderRequest:
    return BlenderRequest(
        request_id=_FAKE_ID,
        script_content="import bpy",
        script_path=_FAKE_SCRIPT,
        output_path=_FAKE_BLEND,
        render_path=_FAKE_RENDER,
        output_dir=_FAKE_DIR,
        timeout=60,
        source_prompt=source_prompt,
    )


def _make_result(status: str = "success", output_path: str | None = _FAKE_BLEND) -> BlenderResult:
    return BlenderResult(
        status=status,
        request_id=_FAKE_ID,
        script_path=_FAKE_SCRIPT,
        output_path=output_path,
        render_path=_FAKE_RENDER if status == "success" else None,
        output_dir=_FAKE_DIR,
        returncode=0 if status == "success" else None,
        stdout=None,
        stderr=None,
        error=None if status == "success" else f"Blender {status}",
    )


# ---------------------------------------------------------------------------
# Tests build_blender_manifest
# ---------------------------------------------------------------------------

def test_manifest_version_and_pipeline():
    manifest = build_blender_manifest(_make_request(), _make_result())
    assert manifest["manifest_version"] == MANIFEST_VERSION
    assert manifest["pipeline"] == PIPELINE


def test_manifest_request_id():
    manifest = build_blender_manifest(_make_request(), _make_result())
    assert manifest["request_id"] == _FAKE_ID


def test_manifest_status_success():
    manifest = build_blender_manifest(_make_request(), _make_result("success"))
    assert manifest["status"] == "success"


def test_manifest_status_error_is_degraded():
    manifest = build_blender_manifest(_make_request(), _make_result("error", None))
    assert manifest["status"] == "degraded"


def test_manifest_status_blender_not_found_is_failed():
    result = BlenderResult(
        status="blender_not_found",
        request_id=_FAKE_ID,
        script_path=_FAKE_SCRIPT,
        output_path=None,
        render_path=None,
        output_dir=_FAKE_DIR,
        returncode=None,
        stdout=None,
        stderr=None,
        error="Blender executable not found.",
    )
    manifest = build_blender_manifest(_make_request(), result)
    assert manifest["status"] == "failed"


def test_manifest_input_prompt_propagated():
    manifest = build_blender_manifest(_make_request("une sphère bleue"), _make_result())
    assert manifest["input"]["prompt"] == "une sphère bleue"


def test_manifest_input_prompt_none_when_missing():
    manifest = build_blender_manifest(_make_request(source_prompt=None), _make_result())
    assert manifest["input"]["prompt"] is None


def test_manifest_input_task_type():
    manifest = build_blender_manifest(_make_request(), _make_result())
    assert manifest["input"]["task_type"] == "blender_script"


def test_manifest_artifacts_keys():
    manifest = build_blender_manifest(_make_request(), _make_result())
    artifacts = manifest["artifacts"]
    assert "scene_py" in artifacts
    assert "scene_blend" in artifacts
    assert "preview_png" in artifacts
    assert "scene_report" in artifacts
    assert "manifest" in artifacts


def test_manifest_execution_blender_status():
    manifest = build_blender_manifest(_make_request(), _make_result("success"))
    assert manifest["execution"]["blender_status"] == "success"


def test_manifest_execution_blender_error_none_on_success():
    manifest = build_blender_manifest(_make_request(), _make_result("success"))
    assert manifest["execution"]["blender_error"] is None


def test_manifest_future_fields_are_null():
    manifest = build_blender_manifest(_make_request(), _make_result())
    future = manifest["future"]
    assert future["creative_intent"] is None
    assert future["template_used"] is None
    assert future["iteration_parent"] is None


def test_manifest_scene_report_unavailable_when_no_meta():
    manifest = build_blender_manifest(_make_request(), _make_result())
    assert manifest["scene_report"]["status"] == "unavailable"
    assert manifest["scene_report"]["violations"] == []


def test_manifest_created_at_is_iso_string():
    manifest = build_blender_manifest(_make_request(), _make_result())
    created_at = manifest.get("created_at", "")
    assert "T" in created_at  # format ISO-8601 basique


def test_manifest_output_dir_present():
    manifest = build_blender_manifest(_make_request(), _make_result())
    assert manifest["output_dir"] == _FAKE_DIR


# ---------------------------------------------------------------------------
# Tests write_blender_manifest
# ---------------------------------------------------------------------------

def test_write_manifest_creates_file(tmp_path):
    output_dir = str(tmp_path / _FAKE_ID)
    os.makedirs(output_dir, exist_ok=True)

    request = BlenderRequest(
        request_id=_FAKE_ID,
        script_content="import bpy",
        script_path=str(tmp_path / _FAKE_ID / "scene.py"),
        output_path=str(tmp_path / _FAKE_ID / "scene.blend"),
        render_path=str(tmp_path / _FAKE_ID / "preview.png"),
        output_dir=output_dir,
        timeout=60,
        source_prompt="une sphère bleue",
    )
    result = BlenderResult(
        status="success",
        request_id=_FAKE_ID,
        script_path=request.script_path,
        output_path=request.output_path,
        render_path=request.render_path,
        output_dir=output_dir,
        returncode=0,
        stdout=None,
        stderr=None,
        error=None,
    )

    manifest_path = write_blender_manifest(request, result)

    assert manifest_path is not None
    assert Path(manifest_path).exists()


def test_write_manifest_content_is_valid_json(tmp_path):
    output_dir = str(tmp_path / _FAKE_ID)
    os.makedirs(output_dir, exist_ok=True)

    request = BlenderRequest(
        request_id=_FAKE_ID,
        script_content="import bpy",
        script_path=str(tmp_path / _FAKE_ID / "scene.py"),
        output_path=str(tmp_path / _FAKE_ID / "scene.blend"),
        render_path=str(tmp_path / _FAKE_ID / "preview.png"),
        output_dir=output_dir,
        timeout=60,
        source_prompt="une sphère bleue",
    )
    result = BlenderResult(
        status="success",
        request_id=_FAKE_ID,
        script_path=request.script_path,
        output_path=request.output_path,
        render_path=request.render_path,
        output_dir=output_dir,
        returncode=0,
        stdout=None,
        stderr=None,
        error=None,
    )

    manifest_path = write_blender_manifest(request, result)
    content = json.loads(Path(manifest_path).read_text(encoding="utf-8"))

    assert content["manifest_version"] == MANIFEST_VERSION
    assert content["status"] == "success"
    assert content["input"]["prompt"] == "une sphère bleue"


def test_write_manifest_on_failure_writes_file(tmp_path):
    """Le manifest doit être écrit même en cas d'échec Blender."""
    output_dir = str(tmp_path / _FAKE_ID)
    os.makedirs(output_dir, exist_ok=True)

    request = BlenderRequest(
        request_id=_FAKE_ID,
        script_content="import bpy",
        script_path=str(tmp_path / _FAKE_ID / "scene.py"),
        output_path=str(tmp_path / _FAKE_ID / "scene.blend"),
        render_path=str(tmp_path / _FAKE_ID / "preview.png"),
        output_dir=output_dir,
        timeout=60,
        source_prompt="une sphère bleue",
    )
    result = BlenderResult(
        status="blender_not_found",
        request_id=_FAKE_ID,
        script_path=request.script_path,
        output_path=None,
        render_path=None,
        output_dir=output_dir,
        returncode=None,
        stdout=None,
        stderr=None,
        error="Blender executable not found.",
    )

    manifest_path = write_blender_manifest(request, result)
    assert manifest_path is not None
    content = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    assert content["status"] == "failed"


def test_write_manifest_io_error_is_non_blocking(tmp_path):
    """Une erreur d'écriture ne doit pas crasher le pipeline."""
    output_dir = str(tmp_path / _FAKE_ID)
    os.makedirs(output_dir, exist_ok=True)

    request = BlenderRequest(
        request_id=_FAKE_ID,
        script_content="import bpy",
        script_path=str(tmp_path / _FAKE_ID / "scene.py"),
        output_path=str(tmp_path / _FAKE_ID / "scene.blend"),
        render_path=str(tmp_path / _FAKE_ID / "preview.png"),
        output_dir=output_dir,
        timeout=60,
    )
    result = BlenderResult(
        status="success",
        request_id=_FAKE_ID,
        script_path=request.script_path,
        output_path=request.output_path,
        render_path=request.render_path,
        output_dir=output_dir,
        returncode=0,
        stdout=None,
        stderr=None,
        error=None,
    )

    with patch("app.engine.artifact_manifest.Path.write_text", side_effect=OSError("disk full")):
        manifest_path = write_blender_manifest(request, result)

    assert manifest_path is None  # échec non bloquant → None


def test_write_manifest_returns_none_when_no_output_dir():
    """Sans output_dir, write_blender_manifest retourne None sans crash."""
    request = BlenderRequest(
        request_id=_FAKE_ID,
        script_content="import bpy",
        script_path=_FAKE_SCRIPT,
        output_path=_FAKE_BLEND,
        render_path=_FAKE_RENDER,
        output_dir="",
        timeout=60,
    )
    result = BlenderResult(
        status="error",
        request_id=_FAKE_ID,
        script_path=None,
        output_path=None,
        render_path=None,
        output_dir=None,
        returncode=None,
        stdout=None,
        stderr=None,
        error="some error",
    )
    manifest_path = write_blender_manifest(request, result)
    assert manifest_path is None


# ---------------------------------------------------------------------------
# Test runtime_debug
# ---------------------------------------------------------------------------

def test_runtime_debug_lists_artifact_manifest():
    from app.engine.runtime_debug import ACTIVE_AUXILIARY_MODULES
    assert "app/engine/artifact_manifest.py" in ACTIVE_AUXILIARY_MODULES


# ---------------------------------------------------------------------------
# H.5.3 — Propagation pipeline_path + product_render_intent dans le manifest
# ---------------------------------------------------------------------------

class TestManifestH53PipelinePathPropagation:
    """
    H.5.3 — Le manifest doit exposer le chemin emprunté par build_blender_script :
      manifest["future"]["pipeline_path"] = "product_render_ir_builder" | "legacy_llm_bpy_scaffold"
      manifest["future"]["product_render_intent"] = dict | None
    """

    def test_manifest_default_pipeline_path_is_legacy(self):
        """Backward compat : un BlenderRequest construit sans pipeline_path
        (anciens appels) doit produire manifest.future.pipeline_path = legacy."""
        request = _make_request()
        result = _make_result()
        manifest = build_blender_manifest(request, result)
        assert manifest["future"]["pipeline_path"] == "legacy_llm_bpy_scaffold"
        assert manifest["future"]["product_render_intent"] is None

    def test_manifest_propagates_builder_pipeline_path(self):
        request = BlenderRequest(
            request_id=_FAKE_ID,
            script_content="import bpy",
            script_path=_FAKE_SCRIPT,
            output_path=_FAKE_BLEND,
            render_path=_FAKE_RENDER,
            output_dir=_FAKE_DIR,
            timeout=60,
            source_prompt="bouteille de parfum ambrée sur fond gris",
            template_used="product_render",
            pipeline_path="product_render_ir_builder",
            product_render_intent={
                "schema_version": "v0",
                "subject": {"kind": "bottle", "color": "amber", "material": "glass"},
                "backdrop": {"color": "neutral_gray"},
            },
        )
        result = _make_result()
        manifest = build_blender_manifest(request, result)
        assert manifest["future"]["pipeline_path"] == "product_render_ir_builder"
        assert manifest["future"]["product_render_intent"] is not None
        assert manifest["future"]["product_render_intent"]["subject"]["kind"] == "bottle"

    def test_manifest_propagates_legacy_pipeline_path_explicitly(self):
        request = BlenderRequest(
            request_id=_FAKE_ID,
            script_content="import bpy",
            script_path=_FAKE_SCRIPT,
            output_path=_FAKE_BLEND,
            render_path=_FAKE_RENDER,
            output_dir=_FAKE_DIR,
            timeout=60,
            source_prompt="anything",
            template_used="interior_space",
            pipeline_path="legacy_llm_bpy_scaffold",
            product_render_intent=None,
        )
        result = _make_result()
        manifest = build_blender_manifest(request, result)
        assert manifest["future"]["pipeline_path"] == "legacy_llm_bpy_scaffold"
        assert manifest["future"]["product_render_intent"] is None


# ---------------------------------------------------------------------------
# Bloc repro (v2) — tiers 1/2/3, best-effort
# ---------------------------------------------------------------------------

class TestReproSection:
    def _patch_probes(self, monkeypatch):
        monkeypatch.setattr("app.engine.repro.aac_git_commit", lambda: "deadbeef" * 5)
        monkeypatch.setattr("app.engine.repro.blender_version", lambda: "Blender 9.9.9")

    def test_repro_block_present_with_fake_paths(self, monkeypatch):
        self._patch_probes(monkeypatch)
        manifest = build_blender_manifest(_make_request(), _make_result())
        block = manifest["repro"]
        assert block["repro_version"] == 1
        assert block["aac_git_commit"] == "deadbeef" * 5
        assert block["blender_version"] == "Blender 9.9.9"
        # Fichiers inexistants -> hashes null, jamais d'exception.
        assert block["scene_py_sha256"] is None
        assert block["preview_png"] == {"sha256": None, "pixels_sha256": None, "dhash": None}
        assert block["scene_report_semantic_sha256"] is None

    def test_repro_hashes_real_artifacts(self, tmp_path, monkeypatch):
        self._patch_probes(monkeypatch)
        from PIL import Image

        from app.engine import repro

        run_dir = tmp_path / "run"
        run_dir.mkdir()
        script = run_dir / "scene.py"
        script.write_text("import bpy\n", encoding="utf-8")
        Image.new("RGB", (16, 16), (10, 120, 200)).save(run_dir / "preview.png")
        report = {
            "template_name": "product_render",
            "object_count": 6,
            "status": "passed",
            "violations": [],
            "scene_report_path": str(run_dir / "scene_report.json"),
        }

        request = _make_request()
        result = BlenderResult(
            status="success",
            request_id=_FAKE_ID,
            script_path=str(script),
            output_path=str(run_dir / "scene.blend"),
            render_path=str(run_dir / "preview.png"),
            output_dir=str(run_dir),
            returncode=0,
            stdout=None,
            stderr=None,
            error=None,
            scene_report=report,
        )

        block = build_blender_manifest(request, result)["repro"]
        assert block["scene_py_sha256"] == repro.sha256_file(script)
        assert block["preview_png"]["sha256"] == repro.sha256_file(run_dir / "preview.png")
        assert block["preview_png"]["dhash"] is not None and len(block["preview_png"]["dhash"]) == 16
        assert block["scene_report_semantic_sha256"] == repro.semantic_scene_report_hash(report)

    def test_semantic_hash_invariant_to_report_paths(self, monkeypatch):
        # Deux runs, même scène, chemins différents -> même hash sémantique.
        self._patch_probes(monkeypatch)
        base_report = {"template_name": "product_render", "object_count": 6, "status": "passed"}

        def manifest_for(report_path: str):
            result = _make_result()
            result = BlenderResult(
                **{**result.__dict__, "scene_report": {**base_report, "scene_report_path": report_path}}
            )
            return build_blender_manifest(_make_request(), result)

        hash_a = manifest_for("/outputs/blender/run-a/scene_report.json")["repro"][
            "scene_report_semantic_sha256"
        ]
        hash_b = manifest_for("/tmp/elsewhere/scene_report.json")["repro"][
            "scene_report_semantic_sha256"
        ]
        assert hash_a is not None and hash_a == hash_b
