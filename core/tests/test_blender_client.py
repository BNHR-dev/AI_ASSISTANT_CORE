"""
Tests : blender_client avec mocks subprocess et filesystem.
Vérifie tous les statuts : success, error, timeout, blender_not_found, no_output.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.clients.blender_client import (
    _extract_python_from_markdown,
    _inject_output_path,
    resolve_blender_exe,
    run_blender_script,
)
from app.engine.blender_types import BlenderRequest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_request(output_path: str = "/tmp/blender/abc/scene.blend") -> BlenderRequest:
    return BlenderRequest(
        request_id="test-abc",
        script_content="import bpy",
        script_path="/tmp/blender/abc/scene.py",
        output_path=output_path,
        output_dir="/tmp/blender/abc",
        timeout=10,
    )


# ---------------------------------------------------------------------------
# resolve_blender_exe
# ---------------------------------------------------------------------------

def test_resolve_blender_exe_absent_returns_none():
    with (
        patch("app.clients.blender_client.BLENDER_EXE", ""),
        patch("app.clients.blender_client._FALLBACK_PATHS", ["/nonexistent/blender"]),
    ):
        result = resolve_blender_exe()
    assert result is None


def test_resolve_blender_exe_env_var(tmp_path):
    fake_exe = tmp_path / "blender"
    fake_exe.write_text("#!/bin/sh")
    with patch("app.clients.blender_client.BLENDER_EXE", str(fake_exe)):
        result = resolve_blender_exe()
    assert result == str(fake_exe)


def test_resolve_blender_exe_fallback(tmp_path):
    fake_exe = tmp_path / "blender"
    fake_exe.write_text("#!/bin/sh")
    with (
        patch("app.clients.blender_client.BLENDER_EXE", ""),
        patch("app.clients.blender_client._FALLBACK_PATHS", [str(fake_exe)]),
    ):
        result = resolve_blender_exe()
    assert result == str(fake_exe)


# ---------------------------------------------------------------------------
# run_blender_script — blender_not_found
# ---------------------------------------------------------------------------

def test_run_blender_script_not_found():
    request = _make_request()
    with patch("app.clients.blender_client.resolve_blender_exe", return_value=None):
        result = run_blender_script(request)
    assert result.status == "blender_not_found"
    assert result.returncode is None
    assert result.output_path is None
    assert result.error is not None


# ---------------------------------------------------------------------------
# run_blender_script — success
# ---------------------------------------------------------------------------

def test_run_blender_script_success(tmp_path):
    output_path = str(tmp_path / "scene.blend")
    request = _make_request(output_path=output_path)

    # Créer le .blend simulé pour que Path.exists() retourne True
    Path(output_path).write_bytes(b"BLEND")

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = "Blender started\nSaved\n"
    mock_proc.stderr = ""

    with (
        patch("app.clients.blender_client.resolve_blender_exe", return_value="/usr/bin/blender"),
        patch("subprocess.run", return_value=mock_proc),
    ):
        result = run_blender_script(request)

    assert result.status == "success"
    assert result.returncode == 0
    assert result.output_path == output_path
    assert result.error is None


# ---------------------------------------------------------------------------
# run_blender_script — error (returncode != 0)
# ---------------------------------------------------------------------------

def test_run_blender_script_error():
    request = _make_request()

    mock_proc = MagicMock()
    mock_proc.returncode = 1
    mock_proc.stdout = ""
    mock_proc.stderr = "Error: Python script failed\n"

    with (
        patch("app.clients.blender_client.resolve_blender_exe", return_value="/usr/bin/blender"),
        patch("subprocess.run", return_value=mock_proc),
    ):
        result = run_blender_script(request)

    assert result.status == "error"
    assert result.returncode == 1
    assert result.output_path is None
    assert "returncode" in result.error


# ---------------------------------------------------------------------------
# run_blender_script — timeout
# ---------------------------------------------------------------------------

def test_run_blender_script_timeout():
    request = _make_request()

    with (
        patch("app.clients.blender_client.resolve_blender_exe", return_value="/usr/bin/blender"),
        patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="blender", timeout=10)),
    ):
        result = run_blender_script(request)

    assert result.status == "timeout"
    assert result.returncode is None
    assert "timeout" in result.error.lower()


# ---------------------------------------------------------------------------
# run_blender_script — no_output (returncode 0 mais pas de .blend)
# ---------------------------------------------------------------------------

def test_run_blender_script_no_output(tmp_path):
    output_path = str(tmp_path / "scene.blend")
    request = _make_request(output_path=output_path)
    # Ne pas créer le fichier → no_output

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = "Blender finished"
    mock_proc.stderr = ""

    with (
        patch("app.clients.blender_client.resolve_blender_exe", return_value="/usr/bin/blender"),
        patch("subprocess.run", return_value=mock_proc),
    ):
        result = run_blender_script(request)

    assert result.status == "no_output"
    assert result.output_path is None


# ---------------------------------------------------------------------------
# Utilitaires internes
# ---------------------------------------------------------------------------

def test_extract_python_from_markdown():
    md = "Voici le script:\n```python\nimport bpy\nprint('hello')\n```\n"
    code = _extract_python_from_markdown(md)
    assert "import bpy" in code
    assert "```" not in code


def test_extract_python_fallback_no_block():
    plain = "import bpy\nprint('hello')"
    code = _extract_python_from_markdown(plain)
    assert code == plain.strip()


def test_inject_output_path_adds_header():
    script = "import bpy\nbpy.ops.mesh.primitive_cube_add()"
    result = _inject_output_path(script, "/tmp/scene.blend")
    assert 'OUTPUT_BLEND_PATH = r"/tmp/scene.blend"' in result
    assert "save_as_mainfile" in result


def test_inject_output_path_replaces_hardcoded():
    script = 'import bpy\nbpy.ops.wm.save_as_mainfile(filepath="/hardcoded/path.blend")'
    result = _inject_output_path(script, "/controlled/scene.blend")
    assert "/hardcoded/path.blend" not in result
    assert "OUTPUT_BLEND_PATH" in result
    # Ne doit pas avoir deux save_as_mainfile
    assert result.count("save_as_mainfile") == 1


def test_inject_output_path_no_double_save():
    """Si save_as_mainfile est déjà présent avec OUTPUT_BLEND_PATH, ne pas le dupliquer."""
    script = "import bpy\nbpy.ops.wm.save_as_mainfile(filepath=OUTPUT_BLEND_PATH)"
    result = _inject_output_path(script, "/tmp/scene.blend")
    assert result.count("save_as_mainfile") == 1
