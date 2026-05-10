"""
Tests : contrat des champs blender_* dans ExecuteResponse (schemas.py).
Vérifie que tous les champs Blender sont bien déclarés et optionnels.
"""
import pytest
from pydantic import ValidationError

from app.schemas import ExecuteResponse


def _base_response(**kwargs) -> dict:
    """Dict minimal valide pour ExecuteResponse."""
    base = {
        "task_type": "blender_script",
        "primary_agent": "AGENT_BUILDER_IA",
        "selected_model": "qwen2.5-coder:7b",
        "needs_web": False,
        "output_format": "blender_script",
        "reason": "test",
        "output": "Fichier .blend produit.",
    }
    base.update(kwargs)
    return base


def test_blender_fields_optional_by_default():
    """Tous les champs blender_* doivent être optionnels (None par défaut)."""
    resp = ExecuteResponse(**_base_response())
    assert resp.blender_status is None
    assert resp.blender_script_path is None
    assert resp.blender_output_path is None
    assert resp.blender_returncode is None
    assert resp.blender_stdout is None
    assert resp.blender_stderr is None
    assert resp.blender_error is None
    assert resp.blender_render_path is None


def test_blender_render_path_populated():
    """blender_render_path peut être peuplé quand le PNG existe."""
    resp = ExecuteResponse(**_base_response(
        blender_status="success",
        blender_render_path="outputs/blender/abc/preview.png",
    ))
    assert resp.blender_render_path == "outputs/blender/abc/preview.png"


def test_blender_fields_populated():
    """Les champs blender_* se peuplent correctement."""
    resp = ExecuteResponse(**_base_response(
        blender_status="success",
        blender_script_path="outputs/blender/abc/scene.py",
        blender_output_path="outputs/blender/abc/scene.blend",
        blender_returncode=0,
        blender_stdout="Saved\n",
        blender_stderr="",
        blender_error=None,
    ))
    assert resp.blender_status == "success"
    assert resp.blender_script_path == "outputs/blender/abc/scene.py"
    assert resp.blender_output_path == "outputs/blender/abc/scene.blend"
    assert resp.blender_returncode == 0
    assert resp.blender_stdout == "Saved\n"
    assert resp.blender_stderr == ""
    assert resp.blender_error is None


def test_blender_error_fields():
    """Champs d'erreur Blender."""
    resp = ExecuteResponse(**_base_response(
        blender_status="blender_not_found",
        blender_error="Blender executable not found.",
        output="Erreur Blender.",
    ))
    assert resp.blender_status == "blender_not_found"
    assert resp.blender_error == "Blender executable not found."


def test_blender_returncode_is_int():
    resp = ExecuteResponse(**_base_response(blender_returncode=1))
    assert isinstance(resp.blender_returncode, int)
    assert resp.blender_returncode == 1


def test_artifact_type_blend_on_success():
    """Quand blender success, artifact_type doit être blend."""
    resp = ExecuteResponse(**_base_response(
        artifact_type="blend",
        artifact_path="outputs/blender/abc/scene.blend",
        artifact_filename="scene.blend",
        blender_status="success",
        blender_output_path="outputs/blender/abc/scene.blend",
        blender_returncode=0,
    ))
    assert resp.artifact_type == "blend"
    assert resp.artifact_path is not None
    assert resp.artifact_filename == "scene.blend"
