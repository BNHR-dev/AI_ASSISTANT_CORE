"""
Tests d'intégration executor : mock build_blender_script + run_blender_script.
Vérifie que execute_request retourne les bons champs artifact pour blender_pipeline.
"""
from __future__ import annotations

from unittest.mock import patch, MagicMock

from app.engine.blender_types import BlenderRequest, BlenderResult


_FAKE_REQUEST_ID = "fake-req-001"
_FAKE_OUTPUT_PATH = f"outputs/blender/{_FAKE_REQUEST_ID}/scene.blend"
_FAKE_SCRIPT_PATH = f"outputs/blender/{_FAKE_REQUEST_ID}/scene.py"
_FAKE_OUTPUT_DIR = f"outputs/blender/{_FAKE_REQUEST_ID}"

_FAKE_BLENDER_REQUEST = BlenderRequest(
    request_id=_FAKE_REQUEST_ID,
    script_content="import bpy",
    script_path=_FAKE_SCRIPT_PATH,
    output_path=_FAKE_OUTPUT_PATH,
    output_dir=_FAKE_OUTPUT_DIR,
    timeout=60,
)

_FAKE_BLENDER_RESULT_SUCCESS = BlenderResult(
    status="success",
    request_id=_FAKE_REQUEST_ID,
    script_path=_FAKE_SCRIPT_PATH,
    output_path=_FAKE_OUTPUT_PATH,
    output_dir=_FAKE_OUTPUT_DIR,
    returncode=0,
    stdout="Blender saved\n",
    stderr="",
    error=None,
)


def _run_with_mocks(message: str, blender_result: BlenderResult):
    from app.engine.executor import execute_request

    with (
        patch(
            "app.clients.blender_client.build_blender_script",
            return_value=_FAKE_BLENDER_REQUEST,
        ),
        patch(
            "app.clients.blender_client.run_blender_script",
            return_value=blender_result,
        ),
        patch("app.engine.step_executor.build_blender_script",
              return_value=_FAKE_BLENDER_REQUEST),
        patch("app.engine.step_executor.run_blender_script",
              return_value=blender_result),
    ):
        return execute_request(message)


def test_execute_returns_blend_artifact_type():
    result = _run_with_mocks(
        "crée une scène Blender avec un cube",
        _FAKE_BLENDER_RESULT_SUCCESS,
    )
    assert result.get("artifact_type") == "blend"


def test_execute_artifact_path_ends_with_scene_blend():
    result = _run_with_mocks(
        "crée une scène Blender avec un cube",
        _FAKE_BLENDER_RESULT_SUCCESS,
    )
    artifact_path = result.get("artifact_path") or ""
    assert artifact_path.endswith("scene.blend"), (
        f"Expected artifact_path ending with scene.blend, got: {artifact_path}"
    )


def test_execute_blender_status_success():
    result = _run_with_mocks(
        "crée une scène Blender avec un cube",
        _FAKE_BLENDER_RESULT_SUCCESS,
    )
    assert result.get("blender_status") == "success"


def test_execute_blender_returncode_zero():
    result = _run_with_mocks(
        "crée une scène Blender avec un cube",
        _FAKE_BLENDER_RESULT_SUCCESS,
    )
    assert result.get("blender_returncode") == 0


def test_execute_blender_error_is_none_on_success():
    result = _run_with_mocks(
        "crée une scène Blender avec un cube",
        _FAKE_BLENDER_RESULT_SUCCESS,
    )
    assert result.get("blender_error") is None


def test_execute_blender_not_found_no_artifact():
    blender_not_found = BlenderResult(
        status="blender_not_found",
        request_id=_FAKE_REQUEST_ID,
        script_path=_FAKE_SCRIPT_PATH,
        output_path=None,
        output_dir=_FAKE_OUTPUT_DIR,
        returncode=None,
        stdout=None,
        stderr=None,
        error="Blender executable not found.",
    )
    result = _run_with_mocks(
        "crée une scène Blender avec un cube",
        blender_not_found,
    )
    assert result.get("artifact_type") is None
    assert result.get("artifact_path") is None
    assert result.get("blender_status") == "blender_not_found"


def test_execute_blender_strategy_is_blender_pipeline():
    result = _run_with_mocks(
        "crée une scène Blender avec un cube",
        _FAKE_BLENDER_RESULT_SUCCESS,
    )
    assert result.get("execution_strategy") == "blender_pipeline"
