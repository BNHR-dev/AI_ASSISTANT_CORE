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
    _inject_output_paths,
    resolve_blender_exe,
    run_blender_script,
)
from app.engine.blender_types import BlenderRequest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_request(
    output_path: str = "/tmp/blender/abc/scene.blend",
    render_path: str = "/tmp/blender/abc/preview.png",
) -> BlenderRequest:
    return BlenderRequest(
        request_id="test-abc",
        script_content="import bpy",
        script_path="/tmp/blender/abc/scene.py",
        output_path=output_path,
        render_path=render_path,
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
    """Les headers OUTPUT_BLEND_PATH, OUTPUT_RENDER_PATH et save_as_mainfile sont toujours présents."""
    script = "import bpy\nbpy.ops.mesh.primitive_cube_add()"
    result = _inject_output_paths(script, "/tmp/scene.blend", "/tmp/preview.png")
    assert 'OUTPUT_BLEND_PATH = r"/tmp/scene.blend"' in result
    assert 'OUTPUT_RENDER_PATH = r"/tmp/preview.png"' in result
    assert "save_as_mainfile" in result


def test_inject_output_path_produces_try_finally():
    """_inject_output_paths doit produire un bloc try/finally."""
    script = "import bpy\nbpy.ops.mesh.primitive_cube_add()"
    result = _inject_output_paths(script, "/tmp/scene.blend", "/tmp/preview.png")
    assert "try:" in result
    assert "finally:" in result


def test_inject_output_path_finally_contains_canonical_literal():
    """Le bloc finally doit contenir les chemins canoniques en string littérale."""
    canonical = "/tmp/outputs/blender/uuid-123/scene.blend"
    render = "/tmp/outputs/blender/uuid-123/preview.png"
    script = "import bpy\nbpy.ops.mesh.primitive_cube_add()"
    result = _inject_output_paths(script, canonical, render)
    finally_idx = result.index("finally:")
    finally_block = result[finally_idx:]
    assert f'r"{canonical}"' in finally_block
    assert f'r"{render}"' in finally_block
    assert "save_as_mainfile" in finally_block
    assert "render.render" in finally_block


def test_inject_output_path_replaces_hardcoded():
    """Les save_as_mainfile avec chemin hardcodé dans le LLM sont neutralisés."""
    script = 'import bpy\nbpy.ops.wm.save_as_mainfile(filepath="/hardcoded/path.blend")'
    result = _inject_output_paths(script, "/controlled/scene.blend", "/controlled/preview.png")
    assert "/hardcoded/path.blend" not in result
    assert r'filepath=r"/controlled/scene.blend"' in result


def test_inject_output_path_no_double_save():
    """Même si save_as_mainfile était déjà présent, le finally force le chemin canonique."""
    script = "import bpy\nbpy.ops.wm.save_as_mainfile(filepath=OUTPUT_BLEND_PATH)"
    result = _inject_output_paths(script, "/tmp/scene.blend", "/tmp/preview.png")
    assert "finally:" in result
    assert r'filepath=r"/tmp/scene.blend"' in result


def test_inject_output_path_llm_overwrites_variable():
    """
    Reproduit le bug terrain : le LLM réécrit OUTPUT_BLEND_PATH = "output_scene.blend".
    Le finally garantit la sauvegarde canonique et le rendu PNG.
    """
    canonical = "outputs/blender/test-uuid-1234/scene.blend"
    render = "outputs/blender/test-uuid-1234/preview.png"
    llm_script = (
        'import bpy\n'
        'OUTPUT_BLEND_PATH = "output_scene.blend"\n'
        'bpy.ops.mesh.primitive_cube_add()\n'
        'bpy.ops.wm.save_as_mainfile(filepath=OUTPUT_BLEND_PATH)\n'
    )
    result = _inject_output_paths(llm_script, canonical, render)

    assert "try:" in result
    assert "finally:" in result
    finally_idx = result.index("finally:")
    finally_block = result[finally_idx:]
    assert f'r"{canonical}"' in finally_block
    assert f'r"{render}"' in finally_block
    assert 'filepath="output_scene.blend"' not in finally_block
    assert "filepath=OUTPUT_BLEND_PATH" not in finally_block


def test_inject_output_path_llm_crash_before_save():
    """
    Même si le script LLM plante avant toute sauvegarde,
    le finally force la sauvegarde .blend et le rendu PNG.
    """
    canonical = "outputs/blender/crash-uuid/scene.blend"
    render = "outputs/blender/crash-uuid/preview.png"
    llm_script = (
        'import bpy\n'
        'mesh = bpy.data.meshes.new("TestMesh")\n'
        'print(mesh.dimensions)\n'
    )
    result = _inject_output_paths(llm_script, canonical, render)

    assert "try:" in result
    assert "finally:" in result
    finally_idx = result.index("finally:")
    finally_block = result[finally_idx:]
    assert f'r"{canonical}"' in finally_block
    assert f'r"{render}"' in finally_block
    assert "save_as_mainfile" in finally_block
    assert "render.render" in finally_block


# ---------------------------------------------------------------------------
# Fallback contenu minimal dans le finally
# ---------------------------------------------------------------------------

def test_inject_output_path_finally_has_mesh_fallback():
    """Le finally doit contenir un fallback conditionnel pour MESH."""
    script = "import bpy\nbpy.ops.mesh.primitive_cube_add()"
    result = _inject_output_paths(script, "/tmp/scene.blend", "/tmp/preview.png")
    finally_idx = result.index("finally:")
    finally_block = result[finally_idx:]
    assert '"MESH"' in finally_block
    assert "primitive_cube_add" in finally_block


def test_inject_output_path_finally_has_camera_fallback():
    """Le finally doit contenir un fallback conditionnel pour CAMERA."""
    script = "import bpy\nbpy.ops.mesh.primitive_cube_add()"
    result = _inject_output_paths(script, "/tmp/scene.blend", "/tmp/preview.png")
    finally_idx = result.index("finally:")
    finally_block = result[finally_idx:]
    assert '"CAMERA"' in finally_block
    assert "camera_add" in finally_block


def test_inject_output_path_finally_has_light_fallback():
    """Le finally doit contenir un fallback conditionnel pour LIGHT."""
    script = "import bpy\nbpy.ops.mesh.primitive_cube_add()"
    result = _inject_output_paths(script, "/tmp/scene.blend", "/tmp/preview.png")
    finally_idx = result.index("finally:")
    finally_block = result[finally_idx:]
    assert '"LIGHT"' in finally_block
    assert "light_add" in finally_block


def test_inject_output_path_fallback_before_save():
    """Les fallbacks précèdent save_as_mainfile, qui précède render.render."""
    script = "import bpy\nbpy.ops.mesh.primitive_cube_add()"
    result = _inject_output_paths(script, "/tmp/scene.blend", "/tmp/preview.png")
    finally_idx = result.index("finally:")
    finally_block = result[finally_idx:]
    mesh_idx = finally_block.index("primitive_cube_add")
    camera_idx = finally_block.index("camera_add")
    light_idx = finally_block.index("light_add")
    save_idx = finally_block.index("save_as_mainfile")
    render_idx = finally_block.index("render.render")
    assert mesh_idx < save_idx
    assert camera_idx < save_idx
    assert light_idx < save_idx
    assert save_idx < render_idx, "save_as_mainfile doit précéder render.render"


def test_inject_output_path_fallback_conditional_not_overwrite():
    """Les fallbacks sont conditionnels (if not any) — ils ne suppriment pas les objets existants."""
    script = "import bpy\nbpy.ops.mesh.primitive_cube_add()"
    result = _inject_output_paths(script, "/tmp/scene.blend", "/tmp/preview.png")
    finally_idx = result.index("finally:")
    finally_block = result[finally_idx:]
    assert finally_block.count("if not any") >= 3


def test_inject_output_paths_finally_has_render_png():
    """Le finally doit contenir le bloc de rendu PNG canonique."""
    render = "/tmp/outputs/blender/uuid/preview.png"
    result = _inject_output_paths("import bpy", "/tmp/scene.blend", render)
    finally_idx = result.index("finally:")
    finally_block = result[finally_idx:]
    assert "file_format" in finally_block
    assert '"PNG"' in finally_block
    assert f'r"{render}"' in finally_block
    assert "render.render" in finally_block


def test_run_blender_script_success_with_render(tmp_path):
    """BlenderResult.render_path est peuplé si preview.png existe."""
    output_path = str(tmp_path / "scene.blend")
    render_path = str(tmp_path / "preview.png")
    request = _make_request(output_path=output_path, render_path=render_path)
    Path(output_path).write_bytes(b"BLEND")
    Path(render_path).write_bytes(b"PNG")

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = "Saved\n"
    mock_proc.stderr = ""

    with (
        patch("app.clients.blender_client.resolve_blender_exe", return_value="/usr/bin/blender"),
        patch("subprocess.run", return_value=mock_proc),
    ):
        result = run_blender_script(request)

    assert result.status == "success"
    assert result.render_path == render_path


def test_run_blender_script_success_no_render(tmp_path):
    """BlenderResult.render_path est None si preview.png n'existe pas (best-effort)."""
    output_path = str(tmp_path / "scene.blend")
    render_path = str(tmp_path / "preview.png")
    request = _make_request(output_path=output_path, render_path=render_path)
    Path(output_path).write_bytes(b"BLEND")
    # Ne pas créer preview.png

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = "Saved\n"
    mock_proc.stderr = ""

    with (
        patch("app.clients.blender_client.resolve_blender_exe", return_value="/usr/bin/blender"),
        patch("subprocess.run", return_value=mock_proc),
    ):
        result = run_blender_script(request)

    assert result.status == "success"
    assert result.render_path is None
