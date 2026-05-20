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
    _BLENDER_SYSTEM_PROMPT,
    _TEMPLATE_FIDELITY_PROMPTS,
    _extract_python_from_markdown,
    _inject_output_paths,
    _render_preview,
    _sanitize_output_blend_path,
    _template_fidelity_block,
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
    """Le bloc finally doit contenir le chemin .blend canonique. Pas de render.render (second subprocess)."""
    canonical = "/tmp/outputs/blender/uuid-123/scene.blend"
    render = "/tmp/outputs/blender/uuid-123/preview.png"
    script = "import bpy\nbpy.ops.mesh.primitive_cube_add()"
    result = _inject_output_paths(script, canonical, render)
    finally_idx = result.index("finally:")
    finally_block = result[finally_idx:]
    assert f'r"{canonical}"' in finally_block
    assert "save_as_mainfile" in finally_block
    assert "render.render" not in finally_block


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
    Le finally garantit la sauvegarde .blend canonique (pas de render.render — second subprocess).
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
    assert 'filepath="output_scene.blend"' not in finally_block
    assert "filepath=OUTPUT_BLEND_PATH" not in finally_block
    assert "render.render" not in finally_block


def test_inject_output_path_llm_crash_before_save():
    """
    Même si le script LLM plante avant toute sauvegarde,
    le finally force la sauvegarde .blend (pas de render.render — second subprocess).
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
    assert "save_as_mainfile" in finally_block
    assert "render.render" not in finally_block


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
    """Les fallbacks précèdent save_as_mainfile dans le finally."""
    script = "import bpy\nbpy.ops.mesh.primitive_cube_add()"
    result = _inject_output_paths(script, "/tmp/scene.blend", "/tmp/preview.png")
    finally_idx = result.index("finally:")
    finally_block = result[finally_idx:]
    mesh_idx = finally_block.index("primitive_cube_add")
    camera_idx = finally_block.index("camera_add")
    light_idx = finally_block.index("light_add")
    save_idx = finally_block.index("save_as_mainfile")
    assert mesh_idx < save_idx
    assert camera_idx < save_idx
    assert light_idx < save_idx
    assert "render.render" not in finally_block


def test_inject_output_path_fallback_conditional_not_overwrite():
    """Les fallbacks sont conditionnels (if not any) — ils ne suppriment pas les objets existants."""
    script = "import bpy\nbpy.ops.mesh.primitive_cube_add()"
    result = _inject_output_paths(script, "/tmp/scene.blend", "/tmp/preview.png")
    finally_idx = result.index("finally:")
    finally_block = result[finally_idx:]
    assert finally_block.count("if not any") >= 3


def test_inject_output_paths_no_render_in_finally():
    """render.render ne doit PAS être dans le finally (second subprocess séparé)."""
    render = "/tmp/outputs/blender/uuid/preview.png"
    result = _inject_output_paths("import bpy", "/tmp/scene.blend", render)
    finally_idx = result.index("finally:")
    finally_block = result[finally_idx:]
    assert "render.render" not in finally_block
    # OUTPUT_RENDER_PATH reste en header pour référence
    assert "OUTPUT_RENDER_PATH" in result


def test_run_blender_script_success_with_render(tmp_path):
    """BlenderResult.render_path est peuplé si _render_preview retourne le chemin PNG."""
    output_path = str(tmp_path / "scene.blend")
    render_path = str(tmp_path / "preview.png")
    request = _make_request(output_path=output_path, render_path=render_path)
    Path(output_path).write_bytes(b"BLEND")

    mock_proc_ok = MagicMock(returncode=0, stdout="Saved\n", stderr="")

    with (
        patch("app.clients.blender_client.resolve_blender_exe", return_value="/usr/bin/blender"),
        patch("subprocess.run", return_value=mock_proc_ok),
        patch("app.clients.blender_client._render_preview", return_value=render_path),
    ):
        result = run_blender_script(request)

    assert result.status == "success"
    assert result.render_path == render_path


def test_run_blender_script_success_no_render(tmp_path):
    """BlenderResult.render_path est None si le second subprocess échoue (best-effort)."""
    output_path = str(tmp_path / "scene.blend")
    render_path = str(tmp_path / "preview.png")
    request = _make_request(output_path=output_path, render_path=render_path)
    Path(output_path).write_bytes(b"BLEND")

    mock_blend_ok = MagicMock(returncode=0, stdout="Saved\n", stderr="")
    mock_render_fail = MagicMock(returncode=-6, stdout="", stderr="Signal 6")
    call_count = 0

    def fake_subprocess_run(cmd, **kwargs):
        nonlocal call_count
        call_count += 1
        return mock_blend_ok if call_count == 1 else mock_render_fail

    with (
        patch("app.clients.blender_client.resolve_blender_exe", return_value="/usr/bin/blender"),
        patch("subprocess.run", side_effect=fake_subprocess_run),
    ):
        result = run_blender_script(request)

    assert result.status == "success"  # pipeline reste success
    assert result.render_path is None  # PNG absent


def test_run_blender_script_render_crash_does_not_fail_pipeline(tmp_path):
    """Un crash du second subprocess PNG ne fait pas échouer blender_status."""
    output_path = str(tmp_path / "scene.blend")
    render_path = str(tmp_path / "preview.png")
    request = _make_request(output_path=output_path, render_path=render_path)
    Path(output_path).write_bytes(b"BLEND")

    with (
        patch("app.clients.blender_client.resolve_blender_exe", return_value="/usr/bin/blender"),
        patch("app.clients.blender_client._render_preview", return_value=None),
        patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="Saved", stderr="")),
    ):
        result = run_blender_script(request)

    assert result.status == "success"
    assert result.render_path is None


# ---------------------------------------------------------------------------
# Structure du script render_preview.py généré par _render_preview()
# On capture le fichier écrit sur disque avant que subprocess soit appelé.
# ---------------------------------------------------------------------------

def _capture_render_script(tmp_path, render_path: str | None = None) -> str:
    """
    Appelle _render_preview() avec un subprocess mocké qui capture le contenu
    de render_preview.py au moment où il est écrit, avant l'exécution.
    Retourne le contenu du script.
    """
    output_path = str(tmp_path / "scene.blend")
    rp = render_path or str(tmp_path / "preview.png")
    Path(output_path).write_bytes(b"BLEND")
    request = _make_request(output_path=output_path, render_path=rp)
    # output_dir doit exister pour que _render_preview puisse écrire le script
    Path(request.output_dir).mkdir(parents=True, exist_ok=True)

    captured = {}

    def fake_subprocess(cmd, **kwargs):
        # Lire le script juste avant qu'il soit exécuté
        script_path = Path(request.output_dir) / "render_preview.py"
        if script_path.exists():
            captured["script"] = script_path.read_text(encoding="utf-8")
        return MagicMock(returncode=0)

    with patch("subprocess.run", side_effect=fake_subprocess):
        _render_preview("/usr/bin/blender", request)

    return captured.get("script", "")


def test_render_preview_script_imports_mathutils(tmp_path):
    """Le script render_preview doit importer mathutils.Vector."""
    script = _capture_render_script(tmp_path)
    assert "from mathutils import Vector" in script


def test_render_preview_script_has_camera_logic(tmp_path):
    """Le script doit gérer une caméra active (vérif + création si absente)."""
    script = _capture_render_script(tmp_path)
    assert "bpy.context.scene.camera" in script
    assert "camera_add" in script


def test_render_preview_script_has_mesh_target(tmp_path):
    """Le script doit calculer la cible à partir des MESH présents."""
    script = _capture_render_script(tmp_path)
    assert '"MESH"' in script
    assert "target" in script


def test_render_preview_script_has_track_quat(tmp_path):
    """Le script doit orienter la caméra avec to_track_quat('-Z', 'Y').to_euler()."""
    script = _capture_render_script(tmp_path)
    assert 'to_track_quat("-Z", "Y").to_euler()' in script


def test_render_preview_script_has_render_filepath(tmp_path):
    """Le script doit configurer render.filepath vers le chemin canonique PNG."""
    render_path = str(tmp_path / "preview.png")
    script = _capture_render_script(tmp_path, render_path=render_path)
    assert f'r"{render_path}"' in script
    assert 'file_format = "PNG"' in script


def test_render_preview_script_calls_render_render(tmp_path):
    """Le script doit appeler render.render(write_still=True)."""
    script = _capture_render_script(tmp_path)
    assert "render.render(write_still=True)" in script


# ---------------------------------------------------------------------------
# Bounding box monde — correction caméra (bound_box + matrix_world)
# ---------------------------------------------------------------------------

def test_render_preview_script_uses_bound_box(tmp_path):
    """Le script doit utiliser bound_box pour calculer le volume réel des meshes."""
    script = _capture_render_script(tmp_path)
    assert "bound_box" in script


def test_render_preview_script_uses_matrix_world(tmp_path):
    """Le script doit appliquer matrix_world pour transformer les coins en coordonnées monde."""
    script = _capture_render_script(tmp_path)
    assert "matrix_world" in script


def test_render_preview_script_computes_radius(tmp_path):
    """Le script doit calculer un radius à partir des bounds pour adapter la distance caméra."""
    script = _capture_render_script(tmp_path)
    assert "radius" in script


def test_render_preview_script_camera_distance_uses_radius(tmp_path):
    """La position de la caméra doit dépendre du radius, pas d'une valeur fixe arbitraire."""
    script = _capture_render_script(tmp_path)
    # distance = max(radius * 2.5, 5.0) puis utilisée pour cam_obj.location
    assert "distance" in script
    assert "cam_obj.location" in script


def test_render_preview_script_camera_min_distance(tmp_path):
    """La distance minimale caméra doit être >= 5.0 pour les scènes simples."""
    script = _capture_render_script(tmp_path)
    assert "5.0" in script


def test_render_preview_script_has_sun_fallback(tmp_path):
    """Le script doit ajouter une lumière SUN si aucune n'existe (best-effort)."""
    script = _capture_render_script(tmp_path)
    assert '"LIGHT"' in script
    assert '"SUN"' in script


def test_render_preview_script_sun_is_conditional(tmp_path):
    """La lumière SUN doit être ajoutée conditionnellement (if not any), pas systématiquement."""
    script = _capture_render_script(tmp_path)
    # La condition doit précéder l'ajout de lumière
    light_check_idx = script.index('"LIGHT"')
    light_add_idx = script.index('"SUN"')
    assert light_check_idx < light_add_idx


# ---------------------------------------------------------------------------
# Nouvelles garanties preview : moteur, résolution, fond world, clipping
# ---------------------------------------------------------------------------

def test_render_preview_script_sets_eevee_engine(tmp_path):
    """Le script doit tenter de configurer le moteur EEVEE (robuste Blender 4.x et 5.x+)."""
    script = _capture_render_script(tmp_path)
    # Le script utilise une détection runtime pour choisir entre BLENDER_EEVEE et BLENDER_EEVEE_NEXT
    assert "BLENDER_EEVEE" in script
    assert "_eevee_engines" in script
    assert "_available_engines" in script


def test_render_preview_script_sets_resolution_512(tmp_path):
    """La résolution doit être fixée à 512x512 pour une preview stable."""
    script = _capture_render_script(tmp_path)
    assert "resolution_x = 512" in script
    assert "resolution_y = 512" in script
    assert "resolution_percentage = 100" in script


def test_render_preview_script_has_world_background(tmp_path):
    """Le script doit configurer un fond world neutre (non noir) pour la preview."""
    script = _capture_render_script(tmp_path)
    assert "bpy.context.scene.world" in script
    assert "use_nodes = True" in script
    assert 'nodes.get("Background")' in script


def test_render_preview_script_world_background_not_black(tmp_path):
    """La couleur de fond ne doit pas être (0,0,0) — utiliser un gris sombre exploitable."""
    script = _capture_render_script(tmp_path)
    # Le fond est (0.05, 0.05, 0.05, 1.0) — pas (0, 0, 0)
    assert "(0.0, 0.0, 0.0, 1.0)" not in script
    assert "0.05" in script


def test_render_preview_script_has_clip_start(tmp_path):
    """Le script doit ajuster clip_start pour éviter les artefacts sur les petits objets."""
    script = _capture_render_script(tmp_path)
    assert "clip_start" in script


def test_render_preview_script_has_clip_end(tmp_path):
    """Le script doit ajuster clip_end pour éviter les coupures sur les grandes scènes."""
    script = _capture_render_script(tmp_path)
    assert "clip_end" in script


def test_render_preview_script_clip_uses_distance(tmp_path):
    """clip_start et clip_end doivent être calculés à partir de la distance caméra (pas hardcodés)."""
    script = _capture_render_script(tmp_path)
    # Les deux doivent référencer 'distance' pour être adaptatifs
    clip_start_idx = script.index("clip_start")
    clip_end_idx = script.index("clip_end")
    # 'distance' doit apparaître après le calcul et avant le clip
    distance_idx = script.index("distance = max(")
    assert distance_idx < clip_start_idx
    assert distance_idx < clip_end_idx


def test_render_preview_script_resolution_before_render(tmp_path):
    """La résolution doit être configurée avant l'appel render.render()."""
    script = _capture_render_script(tmp_path)
    res_idx = script.index("resolution_x = 512")
    render_idx = script.index("render.render(write_still=True)")
    assert res_idx < render_idx


def test_render_preview_script_engine_before_render(tmp_path):
    """La sélection du moteur de rendu doit précéder l'appel render.render()."""
    script = _capture_render_script(tmp_path)
    # La liste _eevee_engines marque le début de la sélection du moteur
    engine_idx = script.index("_eevee_engines")
    render_idx = script.index("render.render(write_still=True)")
    assert engine_idx < render_idx


# ---------------------------------------------------------------------------
# Sanitization OUTPUT_BLEND_PATH — protection déterministe contre les LLM
# ---------------------------------------------------------------------------

def test_sanitize_removes_double_quote_reassignment():
    """OUTPUT_BLEND_PATH = "..." doit être supprimé par la sanitization."""
    script = (
        'import bpy\n'
        'OUTPUT_BLEND_PATH = "path_to_output_file.blend"\n'
        'bpy.ops.mesh.primitive_cube_add()\n'
    )
    result = _sanitize_output_blend_path(script)
    assert 'OUTPUT_BLEND_PATH = "path_to_output_file.blend"' not in result
    assert "import bpy" in result
    assert "primitive_cube_add" in result


def test_sanitize_removes_single_quote_reassignment():
    """OUTPUT_BLEND_PATH = '...' (single quotes) doit être supprimé."""
    script = (
        "import bpy\n"
        "OUTPUT_BLEND_PATH = 'output_scene.blend'\n"
        "bpy.ops.mesh.primitive_cube_add()\n"
    )
    result = _sanitize_output_blend_path(script)
    assert "OUTPUT_BLEND_PATH = 'output_scene.blend'" not in result
    assert "primitive_cube_add" in result


def test_inject_output_paths_only_one_output_blend_path_definition():
    """
    Après _inject_output_paths, le script final doit contenir exactement
    une ligne OUTPUT_BLEND_PATH = ... (le header contrôlé).
    """
    import re as _re
    script = (
        "import bpy\n"
        'OUTPUT_BLEND_PATH = "outputs/foo.blend"\n'
        "bpy.ops.mesh.primitive_cube_add()\n"
        "bpy.ops.wm.save_as_mainfile(filepath=OUTPUT_BLEND_PATH)\n"
    )
    result = _inject_output_paths(script, "/final/scene.blend", "/final/preview.png")
    lines_with_def = [
        line for line in result.splitlines()
        if _re.match(r"\s*OUTPUT_BLEND_PATH\s*=", line)
    ]
    assert len(lines_with_def) == 1
    assert r'OUTPUT_BLEND_PATH = r"/final/scene.blend"' in lines_with_def[0]


# ---------------------------------------------------------------------------
# H.4.3-C — Le prompt système global n'impose plus Wall_*. La consigne murs
# est désormais portée par les blocs de fidélité conditionnels au template.
# ---------------------------------------------------------------------------

def test_system_prompt_does_not_unconditionally_require_walls():
    """Wall_Back / Wall_Left / Wall_Right ne doivent PLUS être imposés par le
    prompt système global : c'est désormais une contrainte spécifique au
    template interior_space."""
    for name in ("Wall_Back", "Wall_Left", "Wall_Right"):
        assert name not in _BLENDER_SYSTEM_PROMPT, (
            f"Le prompt système global ne doit plus mentionner {name} "
            f"comme contrainte inconditionnelle."
        )


def test_fidelity_block_product_render_forbids_walls():
    """Le bloc de fidélité product_render doit interdire Wall_*."""
    block = _template_fidelity_block("product_render")
    assert block, "fidelity block product_render ne doit pas être vide"
    assert "Wall_" in block
    # Le mot 'INTERDIT' ou un terme équivalent doit signaler l'interdiction.
    assert ("INTERDIT" in block) or ("interdit" in block)


def test_fidelity_block_interior_space_requires_walls():
    """Le bloc de fidélité interior_space doit exiger Wall_Back/Left/Right."""
    block = _template_fidelity_block("interior_space")
    assert block, "fidelity block interior_space ne doit pas être vide"
    for name in ("Wall_Back", "Wall_Left", "Wall_Right"):
        assert name in block, (
            f"Le bloc de fidélité interior_space doit exiger {name}"
        )


def test_fidelity_block_none_for_unknown_or_none_template():
    """Template None / inconnu → bloc vide (aucune contrainte spécifique)."""
    assert _template_fidelity_block(None) == ""
    assert _template_fidelity_block("") == ""
    assert _template_fidelity_block("unknown_template_xyz") == ""


def test_fidelity_prompts_cover_known_templates():
    """Sanity : les deux templates supportés ont un bloc défini."""
    assert "product_render" in _TEMPLATE_FIDELITY_PROMPTS
    assert "interior_space" in _TEMPLATE_FIDELITY_PROMPTS


# ---------------------------------------------------------------------------
# H.4.3-C — run_blender_script propage template_used à inspect_blend_scene
# ---------------------------------------------------------------------------

def test_run_blender_script_propagates_template_used_to_inspector(tmp_path):
    """Le pipeline doit transmettre request.template_used à inspect_blend_scene
    pour permettre la QA statique scene.py vs template."""
    output_path = str(tmp_path / "scene.blend")
    render_path = str(tmp_path / "preview.png")
    request = BlenderRequest(
        request_id="test-h43c",
        script_content="import bpy",
        script_path=str(tmp_path / "scene.py"),
        output_path=output_path,
        render_path=render_path,
        output_dir=str(tmp_path),
        timeout=10,
        template_used="product_render",
    )
    Path(output_path).write_bytes(b"BLEND")

    mock_proc_ok = MagicMock(returncode=0, stdout="Saved\n", stderr="")
    captured_kwargs: dict = {}

    def fake_inspect(*args, **kwargs):
        captured_kwargs.update(kwargs)
        # Retour minimaliste compatible avec _write_report
        return {
            "status": "passed",
            "violations": [],
            "scene_report_path": str(tmp_path / "scene_report.json"),
        }

    with (
        patch("app.clients.blender_client.resolve_blender_exe", return_value="/usr/bin/blender"),
        patch("subprocess.run", return_value=mock_proc_ok),
        patch("app.clients.blender_client._render_preview", return_value=None),
        patch("app.clients.blender_client.inspect_blend_scene", side_effect=fake_inspect),
    ):
        result = run_blender_script(request)

    assert result.status == "success"
    assert captured_kwargs.get("template_name") == "product_render", (
        "run_blender_script doit transmettre template_used=product_render "
        "à inspect_blend_scene sous le kwarg template_name."
    )
