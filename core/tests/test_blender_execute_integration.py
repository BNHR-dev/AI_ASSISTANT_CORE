"""
Tests d'intégration executor : mock build_blender_script + run_blender_script.
Vérifie que execute_request retourne les bons champs artifact pour blender_pipeline.
"""
from __future__ import annotations

from unittest.mock import patch

from app.engine.artifact_manifest import MANIFEST_VERSION
from app.engine.blender_types import BlenderRequest, BlenderResult


_FAKE_REQUEST_ID = "fake-req-001"
_FAKE_OUTPUT_PATH = f"outputs/blender/{_FAKE_REQUEST_ID}/scene.blend"
_FAKE_SCRIPT_PATH = f"outputs/blender/{_FAKE_REQUEST_ID}/scene.py"
_FAKE_OUTPUT_DIR = f"outputs/blender/{_FAKE_REQUEST_ID}"

_FAKE_RENDER_PATH = f"outputs/blender/{_FAKE_REQUEST_ID}/preview.png"

_FAKE_BLENDER_REQUEST = BlenderRequest(
    request_id=_FAKE_REQUEST_ID,
    script_content="import bpy",
    script_path=_FAKE_SCRIPT_PATH,
    output_path=_FAKE_OUTPUT_PATH,
    render_path=_FAKE_RENDER_PATH,
    output_dir=_FAKE_OUTPUT_DIR,
    timeout=60,
)

_FAKE_MANIFEST_PATH = f"outputs/blender/{_FAKE_REQUEST_ID}/manifest.json"

_FAKE_MANIFEST_DATA = {
    "manifest_version": MANIFEST_VERSION,
    "pipeline": "blender",
    "request_id": _FAKE_REQUEST_ID,
    "status": "success",
    "input": {"prompt": "crée une scène Blender avec un cube", "task_type": "blender_script"},
    "artifacts": {},
    "scene_report": {"status": "unavailable", "violations": []},
    "execution": {"blender_status": "success", "blender_error": None},
    "future": {"creative_intent": None, "template_used": None, "iteration_parent": None},
}

_FAKE_BLENDER_RESULT_SUCCESS = BlenderResult(
    status="success",
    request_id=_FAKE_REQUEST_ID,
    script_path=_FAKE_SCRIPT_PATH,
    output_path=_FAKE_OUTPUT_PATH,
    render_path=_FAKE_RENDER_PATH,
    output_dir=_FAKE_OUTPUT_DIR,
    returncode=0,
    stdout="Blender saved\n",
    stderr="",
    error=None,
    manifest_path=_FAKE_MANIFEST_PATH,
)


def _run_with_mocks(message: str, blender_result: BlenderResult):
    import json
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
        patch(
            "app.engine.executor.Path",
            **{"return_value.read_text.return_value": json.dumps(_FAKE_MANIFEST_DATA)},
        ),
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


def test_execute_blender_render_path_exposed():
    """blender_render_path est exposé dans le résultat quand le PNG existe."""
    result = _run_with_mocks(
        "crée une scène Blender avec un cube",
        _FAKE_BLENDER_RESULT_SUCCESS,
    )
    assert result.get("blender_render_path") == _FAKE_RENDER_PATH


def test_execute_blender_not_found_no_artifact():
    blender_not_found = BlenderResult(
        status="blender_not_found",
        request_id=_FAKE_REQUEST_ID,
        script_path=_FAKE_SCRIPT_PATH,
        output_path=None,
        render_path=None,
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


def test_execute_exposes_blender_scene_report_key():
    """blender_scene_report est présent dans le résultat /execute (peut être None si pas de mock)."""
    result = _run_with_mocks(
        "crée une scène Blender avec un cube",
        _FAKE_BLENDER_RESULT_SUCCESS,
    )
    assert "blender_scene_report" in result


def test_execute_blender_scene_report_passed_when_mocked():
    """Quand BlenderResult porte un scene_report valide,
    blender_scene_report est exposé avec status=passed dans /execute."""
    _FAKE_REPORT = {
        "status": "passed",
        "violations": [],
        "object_count": 3,
        "mesh_count": 1,
        "camera_count": 1,
        "light_count": 1,
        "has_active_camera": True,
        "object_names": ["Cube", "Camera", "Key_Light"],
        "scene_blend_exists": True,
        "scene_py_exists": True,
        "scene_report_path": f"{_FAKE_OUTPUT_DIR}/scene_report.json",
    }

    _FAKE_RESULT_WITH_REPORT = BlenderResult(
        status="success",
        request_id=_FAKE_REQUEST_ID,
        script_path=_FAKE_SCRIPT_PATH,
        output_path=_FAKE_OUTPUT_PATH,
        render_path=_FAKE_RENDER_PATH,
        output_dir=_FAKE_OUTPUT_DIR,
        returncode=0,
        stdout="Blender saved\n",
        stderr="",
        error=None,
        scene_report=_FAKE_REPORT,
        scene_report_path=_FAKE_REPORT["scene_report_path"],
    )

    result = _run_with_mocks(
        "crée une scène Blender avec un cube",
        _FAKE_RESULT_WITH_REPORT,
    )
    report = result.get("blender_scene_report")
    assert report is not None, "blender_scene_report doit être exposé"
    assert report["status"] == "passed"
    assert report["violations"] == []


def test_execute_exposes_blender_scene_report_path_key():
    """blender_scene_report_path est présent dans le résultat /execute."""
    result = _run_with_mocks(
        "crée une scène Blender avec un cube",
        _FAKE_BLENDER_RESULT_SUCCESS,
    )
    assert "blender_scene_report_path" in result


def test_execute_exposes_blender_manifest_path():
    result = _run_with_mocks(
        "crée une scène Blender avec un cube",
        _FAKE_BLENDER_RESULT_SUCCESS,
    )
    assert result.get("blender_manifest_path") == _FAKE_MANIFEST_PATH


def test_execute_exposes_blender_manifest_with_expected_keys():
    result = _run_with_mocks(
        "crée une scène Blender avec un cube",
        _FAKE_BLENDER_RESULT_SUCCESS,
    )
    manifest = result.get("blender_manifest")
    assert isinstance(manifest, dict), f"Expected dict, got: {type(manifest)}"
    assert manifest.get("manifest_version") == MANIFEST_VERSION
    assert manifest.get("pipeline") == "blender"
    assert manifest.get("status") == "success"
