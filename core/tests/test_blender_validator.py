"""
Tests unitaires — blender_validator.inspect_blend_scene.

Tous les tests utilisent des mocks : aucun Blender réel requis.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.engine.blender_validator import (
    V_MISSING_BLEND_FILE,
    V_MISSING_SCENE_PY,
    V_NO_CAMERA,
    V_NO_ACTIVE_CAMERA,
    V_NO_MESH,
    V_NO_OBJECTS,
    V_NO_LIGHT,
    V_SUBPROCESS_ERROR,
    V_INVALID_REPORT_JSON,
    V_TIMEOUT,
    inspect_blend_scene,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_bpy_report(
    object_count: int = 3,
    mesh_count: int = 1,
    camera_count: int = 1,
    light_count: int = 1,
    has_active_camera: bool = True,
    object_names: list | None = None,
) -> dict:
    return {
        "object_count": object_count,
        "mesh_count": mesh_count,
        "camera_count": camera_count,
        "light_count": light_count,
        "has_active_camera": has_active_camera,
        "object_names": object_names or ["Cube", "Camera", "Key_Light"],
    }


def _make_proc_success(tmp_path: Path, bpy_report: dict):
    """
    Retourne un mock subprocess.CompletedProcess qui écrit bpy_report
    dans le fichier temporaire passé au script d'inspection.

    On mocke subprocess.run avec un side_effect qui écrit dans le fichier JSON
    attendu par inspect_blend_scene (passé via le script .py).
    """
    def _run_side_effect(cmd, **kwargs):
        # Le script d'inspection écrit dans un fichier temporaire.
        # On retrouve le chemin du script d'inspection dans cmd[-1],
        # puis on lit le contenu du script pour extraire le chemin du rapport.
        script_path = cmd[-1]
        try:
            script_content = Path(script_path).read_text(encoding="utf-8")
            # Extraire le chemin du rapport JSON depuis le script
            import re
            m = re.search(r"open\((.+?),", script_content)
            if m:
                report_path = m.group(1).strip().strip("'\"")
                Path(report_path).write_text(
                    json.dumps(bpy_report), encoding="utf-8"
                )
        except Exception:
            pass
        result = MagicMock()
        result.returncode = 0
        result.stdout = ""
        result.stderr = ""
        return result
    return _run_side_effect


# ---------------------------------------------------------------------------
# Test : blend absent → failed + missing_blend_file
# ---------------------------------------------------------------------------

def test_inspect_returns_failed_when_blend_missing(tmp_path):
    fake_blend = str(tmp_path / "scene.blend")  # n'existe pas
    report = inspect_blend_scene("blender", fake_blend, str(tmp_path), 30)
    assert report["status"] == "failed"
    assert V_MISSING_BLEND_FILE in report["violations"]


# ---------------------------------------------------------------------------
# Test : subprocess returncode != 0 → failed + subprocess_error
# ---------------------------------------------------------------------------

def test_inspect_returns_failed_on_subprocess_error(tmp_path):
    blend_path = tmp_path / "scene.blend"
    blend_path.write_bytes(b"FAKE")

    failing_proc = MagicMock()
    failing_proc.returncode = 1
    failing_proc.stdout = ""
    failing_proc.stderr = "Error"

    with patch("app.engine.blender_validator.subprocess.run", return_value=failing_proc):
        report = inspect_blend_scene("blender", str(blend_path), str(tmp_path), 30)

    assert report["status"] == "failed"
    assert V_SUBPROCESS_ERROR in report["violations"]


# ---------------------------------------------------------------------------
# Test : TimeoutExpired → failed + timeout
# ---------------------------------------------------------------------------

def test_inspect_returns_failed_on_timeout(tmp_path):
    blend_path = tmp_path / "scene.blend"
    blend_path.write_bytes(b"FAKE")

    with patch(
        "app.engine.blender_validator.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="blender", timeout=30),
    ):
        report = inspect_blend_scene("blender", str(blend_path), str(tmp_path), 30)

    assert report["status"] == "failed"
    assert V_TIMEOUT in report["violations"]


# ---------------------------------------------------------------------------
# Test : JSON invalide produit par le script bpy → failed + invalid_report_json
# ---------------------------------------------------------------------------

def test_inspect_returns_failed_on_invalid_json(tmp_path):
    blend_path = tmp_path / "scene.blend"
    blend_path.write_bytes(b"FAKE")

    def _bad_json_run(cmd, **kwargs):
        # Écrire du JSON invalide dans le fichier temporaire
        script_path = cmd[-1]
        try:
            import re
            content = Path(script_path).read_text(encoding="utf-8")
            m = re.search(r"open\((.+?),", content)
            if m:
                report_path = m.group(1).strip().strip("'\"")
                Path(report_path).write_text("NOT_JSON{{{", encoding="utf-8")
        except Exception:
            pass
        result = MagicMock()
        result.returncode = 0
        return result

    with patch("app.engine.blender_validator.subprocess.run", side_effect=_bad_json_run):
        report = inspect_blend_scene("blender", str(blend_path), str(tmp_path), 30)

    assert report["status"] == "failed"
    assert V_INVALID_REPORT_JSON in report["violations"]


# ---------------------------------------------------------------------------
# Test : scène minimale valide → passed, pas de violations
# ---------------------------------------------------------------------------

def test_inspect_returns_passed_for_minimal_scene(tmp_path):
    blend_path = tmp_path / "scene.blend"
    blend_path.write_bytes(b"FAKE")
    (tmp_path / "scene.py").write_text("# scene", encoding="utf-8")

    bpy_report = _fake_bpy_report()

    with patch(
        "app.engine.blender_validator.subprocess.run",
        side_effect=_make_proc_success(tmp_path, bpy_report),
    ):
        report = inspect_blend_scene("blender", str(blend_path), str(tmp_path), 30)

    assert report["status"] == "passed"
    assert report["violations"] == []
    assert report["object_count"] == 3
    assert report["mesh_count"] == 1
    assert report["camera_count"] == 1
    assert report["light_count"] == 1
    assert report["has_active_camera"] is True


# ---------------------------------------------------------------------------
# Test : pas de caméra → no_camera + no_active_camera dans violations
# ---------------------------------------------------------------------------

def test_inspect_detects_no_camera_violation(tmp_path):
    blend_path = tmp_path / "scene.blend"
    blend_path.write_bytes(b"FAKE")

    bpy_report = _fake_bpy_report(
        camera_count=0,
        has_active_camera=False,
        object_names=["Cube", "Key_Light"],
    )

    with patch(
        "app.engine.blender_validator.subprocess.run",
        side_effect=_make_proc_success(tmp_path, bpy_report),
    ):
        report = inspect_blend_scene("blender", str(blend_path), str(tmp_path), 30)

    assert report["status"] == "degraded"
    assert V_NO_CAMERA in report["violations"]
    assert V_NO_ACTIVE_CAMERA in report["violations"]


# ---------------------------------------------------------------------------
# Test : pas de mesh → no_mesh dans violations
# ---------------------------------------------------------------------------

def test_inspect_detects_no_mesh_violation(tmp_path):
    blend_path = tmp_path / "scene.blend"
    blend_path.write_bytes(b"FAKE")

    bpy_report = _fake_bpy_report(
        mesh_count=0,
        object_names=["Camera", "Key_Light"],
    )

    with patch(
        "app.engine.blender_validator.subprocess.run",
        side_effect=_make_proc_success(tmp_path, bpy_report),
    ):
        report = inspect_blend_scene("blender", str(blend_path), str(tmp_path), 30)

    assert report["status"] == "degraded"
    assert V_NO_MESH in report["violations"]


# ---------------------------------------------------------------------------
# Test : pas de lumière → no_light dans violations
# ---------------------------------------------------------------------------

def test_inspect_detects_no_light_violation(tmp_path):
    blend_path = tmp_path / "scene.blend"
    blend_path.write_bytes(b"FAKE")

    bpy_report = _fake_bpy_report(light_count=0, object_names=["Cube", "Camera"])

    with patch(
        "app.engine.blender_validator.subprocess.run",
        side_effect=_make_proc_success(tmp_path, bpy_report),
    ):
        report = inspect_blend_scene("blender", str(blend_path), str(tmp_path), 30)

    assert report["status"] == "degraded"
    assert V_NO_LIGHT in report["violations"]


# ---------------------------------------------------------------------------
# Test : scène vide → no_objects + no_mesh + no_camera + no_active_camera + no_light
# ---------------------------------------------------------------------------

def test_inspect_detects_empty_scene(tmp_path):
    blend_path = tmp_path / "scene.blend"
    blend_path.write_bytes(b"FAKE")

    bpy_report = _fake_bpy_report(
        object_count=0,
        mesh_count=0,
        camera_count=0,
        light_count=0,
        has_active_camera=False,
        object_names=[],
    )

    with patch(
        "app.engine.blender_validator.subprocess.run",
        side_effect=_make_proc_success(tmp_path, bpy_report),
    ):
        report = inspect_blend_scene("blender", str(blend_path), str(tmp_path), 30)

    assert V_NO_OBJECTS in report["violations"]
    assert V_NO_MESH in report["violations"]
    assert V_NO_CAMERA in report["violations"]
    assert V_NO_ACTIVE_CAMERA in report["violations"]
    assert V_NO_LIGHT in report["violations"]


# ---------------------------------------------------------------------------
# Test : scene_report.json écrit dans output_dir
# ---------------------------------------------------------------------------

def test_inspect_writes_scene_report_json(tmp_path):
    blend_path = tmp_path / "scene.blend"
    blend_path.write_bytes(b"FAKE")

    bpy_report = _fake_bpy_report()

    with patch(
        "app.engine.blender_validator.subprocess.run",
        side_effect=_make_proc_success(tmp_path, bpy_report),
    ):
        report = inspect_blend_scene("blender", str(blend_path), str(tmp_path), 30)

    report_json = tmp_path / "scene_report.json"
    assert report_json.exists(), "scene_report.json doit être écrit dans output_dir"

    written = json.loads(report_json.read_text(encoding="utf-8"))
    assert written["status"] == report["status"]


# ---------------------------------------------------------------------------
# Test : scene_report.json est écrit même quand blend absent
# ---------------------------------------------------------------------------

def test_inspect_writes_scene_report_json_even_when_blend_missing(tmp_path):
    fake_blend = str(tmp_path / "scene.blend")  # n'existe pas
    inspect_blend_scene("blender", fake_blend, str(tmp_path), 30)

    report_json = tmp_path / "scene_report.json"
    assert report_json.exists(), "scene_report.json doit être écrit même si .blend absent"
    written = json.loads(report_json.read_text(encoding="utf-8"))
    assert written["status"] == "failed"


# ---------------------------------------------------------------------------
# Test : timeout borné à 30 même si timeout pipeline > 30
# ---------------------------------------------------------------------------

def test_inspect_timeout_is_bounded_to_30(tmp_path):
    blend_path = tmp_path / "scene.blend"
    blend_path.write_bytes(b"FAKE")

    calls = []

    def _capture_timeout(cmd, **kwargs):
        calls.append(kwargs.get("timeout"))
        result = MagicMock()
        result.returncode = 1
        return result

    with patch("app.engine.blender_validator.subprocess.run", side_effect=_capture_timeout):
        inspect_blend_scene("blender", str(blend_path), str(tmp_path), timeout=120)

    assert calls, "subprocess.run doit avoir été appelé"
    assert calls[0] <= 30, f"timeout devrait être ≤ 30, got {calls[0]}"


# ---------------------------------------------------------------------------
# Test : missing_scene_py reporté si scene.py absent
# ---------------------------------------------------------------------------

def test_inspect_detects_missing_scene_py(tmp_path):
    blend_path = tmp_path / "scene.blend"
    blend_path.write_bytes(b"FAKE")
    # Pas de scene.py dans tmp_path

    bpy_report = _fake_bpy_report()

    with patch(
        "app.engine.blender_validator.subprocess.run",
        side_effect=_make_proc_success(tmp_path, bpy_report),
    ):
        report = inspect_blend_scene("blender", str(blend_path), str(tmp_path), 30)

    assert V_MISSING_SCENE_PY in report["violations"]
    # Le statut reste degraded (pas failed — le .blend est là et lisible)
    assert report["status"] == "degraded"


# ---------------------------------------------------------------------------
# Test : scene_report_path exposé dans le rapport
# ---------------------------------------------------------------------------

def test_inspect_exposes_scene_report_path(tmp_path):
    blend_path = tmp_path / "scene.blend"
    blend_path.write_bytes(b"FAKE")

    bpy_report = _fake_bpy_report()

    with patch(
        "app.engine.blender_validator.subprocess.run",
        side_effect=_make_proc_success(tmp_path, bpy_report),
    ):
        report = inspect_blend_scene("blender", str(blend_path), str(tmp_path), 30)

    assert "scene_report_path" in report
    assert report["scene_report_path"].endswith("scene_report.json")
