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
    assert "_pf_scene.world" in script
    assert "use_nodes = True" in script
    assert 'nodes.get("Background")' in script


def test_render_preview_script_world_background_not_black(tmp_path):
    """La couleur de fond ne doit pas être (0,0,0). Depuis H.6.11 le fond plat est
    remplacé par un gradient directionnel borné (0.03→0.12) — toujours gris sombre
    exploitable, jamais noir."""
    script = _capture_render_script(tmp_path)
    assert "(0.0, 0.0, 0.0, 1.0)" not in script
    # H.6.11 : gradient borné, extrémité basse non nulle (gris sombre exploitable)
    assert "(0.03, 0.03, 0.03, 1.0)" in script


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


# ---------------------------------------------------------------------------
# H.5.3 — Branchement product_render IR extractor + builder
# ---------------------------------------------------------------------------

class TestBuildBlenderScriptH53Branching:
    """
    Vérifie le branchement product_render IR introduit en H.5.3 :
    - product_render + extraction parsed → chemin builder, pipeline_path renseigné
    - product_render + extraction fallback → chemin legacy
    - product_render + builder exception → chemin legacy
    - template != product_render → chemin legacy (jamais d'extracteur appelé)

    Tous les tests sont mockés. Pas d'Ollama réel, pas de Blender, pas de réseau.
    """

    def _setup_basic_mocks(self, tmp_path, mock_intent_dict=None):
        """Patches minimal communs aux 4 cas : output dir + parse_artistic_intent
        + write_intent_json. Retourne le dict de patches actifs pour ajout."""
        from app.engine.product_render_ir import (
            BackdropIR,
            ProductRenderIntent,
            ProductSubjectIR,
        )
        # Fake ArtisticIntent : on instancie le vrai modèle Pydantic ou un mock
        # avec .model_dump() ; ici on patch carrément parse_artistic_intent.
        fake_intent = MagicMock()
        fake_intent.model_dump.return_value = mock_intent_dict or {"medium": "product_render"}
        return fake_intent

    def test_legacy_path_when_template_is_not_product_render(self, tmp_path, monkeypatch):
        """Template != product_render → l'extracteur n'est PAS invoqué,
        pipeline_path reste legacy, product_render_intent reste None."""
        from app.clients import blender_client as bc

        # Force output dir vers tmp_path pour éviter d'écrire dans outputs/
        monkeypatch.setattr(bc, "BLENDER_OUTPUT_DIR", str(tmp_path))
        monkeypatch.setattr(bc, "BLENDER_USE_PRODUCT_RENDER_IR", True)

        fake_intent = self._setup_basic_mocks(tmp_path)
        extractor_calls = {"count": 0}

        def _track_extractor(*args, **kwargs):
            extractor_calls["count"] += 1
            raise AssertionError("extract_product_render_intent should NOT be called")

        with patch.object(bc, "parse_artistic_intent", return_value=fake_intent), \
             patch.object(bc, "write_intent_json"), \
             patch.object(bc, "select_template_from_intent", return_value=None), \
             patch.object(bc, "get_template_name_from_intent", return_value=None), \
             patch.object(bc, "select_template", return_value=None), \
             patch.object(bc, "get_template_name", return_value=None), \
             patch.object(bc, "generate_with_ollama", return_value="```python\nimport bpy\n```"), \
             patch.object(bc, "extract_product_render_intent", side_effect=_track_extractor):
            req = bc.build_blender_script(
                message="quelque chose sans template",
                context={},
                request_id="t-h53-legacy",
            )

        assert req.pipeline_path == "legacy_llm_bpy_scaffold"
        assert req.product_render_intent is None
        assert extractor_calls["count"] == 0, (
            "extract_product_render_intent ne doit JAMAIS être appelé "
            "quand selected_template_name != 'product_render'"
        )

    def test_builder_path_when_product_render_and_extraction_parsed(self, tmp_path, monkeypatch):
        """product_render + extraction.status == 'parsed' → chemin builder,
        pipeline_path = product_render_ir_builder, product_render_intent renseigné,
        generate_with_ollama N'EST PAS appelé pour le scaffold."""
        from app.clients import blender_client as bc
        from app.engine.product_render_extractor import ProductRenderExtractionResult
        from app.engine.product_render_ir import (
            BackdropIR,
            ProductRenderIntent,
            ProductSubjectIR,
        )

        monkeypatch.setattr(bc, "BLENDER_OUTPUT_DIR", str(tmp_path))
        monkeypatch.setattr(bc, "BLENDER_USE_PRODUCT_RENDER_IR", True)

        fake_intent = self._setup_basic_mocks(tmp_path)

        # IR parsé que l'extracteur retourne
        parsed_ir = ProductRenderIntent(
            schema_version="v0",
            subject=ProductSubjectIR(kind="bottle", color="amber", material="glass"),
            backdrop=BackdropIR(color="neutral_gray"),
        )
        extraction_result = ProductRenderExtractionResult(
            intent=parsed_ir,
            status="parsed",
            raw_response="raw llm",
            extracted_json={"schema_version": "v0"},
            error=None,
            model="qwen2.5-coder:7b",
        )

        ollama_calls = {"count": 0}

        def _track_ollama(*args, **kwargs):
            ollama_calls["count"] += 1
            return "```python\nimport bpy\n```"

        with patch.object(bc, "parse_artistic_intent", return_value=fake_intent), \
             patch.object(bc, "write_intent_json"), \
             patch.object(bc, "select_template_from_intent", return_value="<scaffold-stub>"), \
             patch.object(bc, "get_template_name_from_intent", return_value="product_render"), \
             patch.object(bc, "extract_product_render_intent", return_value=extraction_result), \
             patch.object(
                 bc,
                 "build_product_render_scene_script",
                 return_value="import bpy\n# H.5.1 deterministic script\n",
             ), \
             patch.object(bc, "generate_with_ollama", side_effect=_track_ollama):
            req = bc.build_blender_script(
                message="bouteille de parfum ambrée sur fond gris",
                context={},
                request_id="t-h53-builder",
            )

        assert req.pipeline_path == "product_render_ir_builder"
        assert req.product_render_intent is not None
        assert req.product_render_intent["subject"]["kind"] == "bottle"
        assert req.product_render_intent["subject"]["color"] == "amber"
        assert ollama_calls["count"] == 0, (
            "generate_with_ollama NE DOIT PAS être appelé quand le chemin "
            "builder est emprunté (sinon double appel LLM gratuit)."
        )

    def test_legacy_path_when_extraction_returns_fallback(self, tmp_path, monkeypatch):
        """product_render + extraction.status == 'fallback' → retombe sur
        le scaffold prompt-only legacy (pas sur le FALLBACK_INTENT canonique
        qui forcerait bottle/amber/glass indépendamment de la demande)."""
        from app.clients import blender_client as bc
        from app.engine.product_render_extractor import (
            FALLBACK_INTENT,
            ProductRenderExtractionResult,
        )

        monkeypatch.setattr(bc, "BLENDER_OUTPUT_DIR", str(tmp_path))
        monkeypatch.setattr(bc, "BLENDER_USE_PRODUCT_RENDER_IR", True)

        fake_intent = self._setup_basic_mocks(tmp_path)

        fallback_result = ProductRenderExtractionResult(
            intent=FALLBACK_INTENT,
            status="fallback",
            raw_response="not json",
            extracted_json=None,
            error="json_decode_error: ...",
            model="qwen2.5-coder:7b",
        )

        builder_calls = {"count": 0}

        def _track_builder(*args, **kwargs):
            builder_calls["count"] += 1
            return "import bpy\n"

        with patch.object(bc, "parse_artistic_intent", return_value=fake_intent), \
             patch.object(bc, "write_intent_json"), \
             patch.object(bc, "select_template_from_intent", return_value="<scaffold-stub>"), \
             patch.object(bc, "get_template_name_from_intent", return_value="product_render"), \
             patch.object(bc, "extract_product_render_intent", return_value=fallback_result), \
             patch.object(bc, "build_product_render_scene_script", side_effect=_track_builder), \
             patch.object(bc, "generate_with_ollama", return_value="```python\nimport bpy\n```"):
            req = bc.build_blender_script(
                message="autre demande qui fait planter l'extracteur",
                context={},
                request_id="t-h53-fallback",
            )

        assert req.pipeline_path == "legacy_llm_bpy_scaffold"
        assert req.product_render_intent is None
        assert builder_calls["count"] == 0, (
            "build_product_render_scene_script NE DOIT PAS être appelé "
            "quand extraction.status == 'fallback'."
        )

    def test_legacy_path_when_builder_raises(self, tmp_path, monkeypatch):
        """product_render + extraction parsed mais build_product_render_scene_script
        lève une exception → catch + fallback legacy. Jamais de crash."""
        from app.clients import blender_client as bc
        from app.engine.product_render_extractor import ProductRenderExtractionResult
        from app.engine.product_render_ir import (
            BackdropIR,
            ProductRenderIntent,
            ProductSubjectIR,
        )

        monkeypatch.setattr(bc, "BLENDER_OUTPUT_DIR", str(tmp_path))
        monkeypatch.setattr(bc, "BLENDER_USE_PRODUCT_RENDER_IR", True)

        fake_intent = self._setup_basic_mocks(tmp_path)

        parsed_ir = ProductRenderIntent(
            schema_version="v0",
            subject=ProductSubjectIR(kind="bottle", color="amber", material="glass"),
            backdrop=BackdropIR(color="neutral_gray"),
        )
        extraction_result = ProductRenderExtractionResult(
            intent=parsed_ir,
            status="parsed",
            raw_response="raw",
            extracted_json={"schema_version": "v0"},
            error=None,
            model="qwen2.5-coder:7b",
        )

        def _builder_explodes(intent):
            raise RuntimeError("simulated builder bug for H.5.3 test")

        with patch.object(bc, "parse_artistic_intent", return_value=fake_intent), \
             patch.object(bc, "write_intent_json"), \
             patch.object(bc, "select_template_from_intent", return_value="<scaffold-stub>"), \
             patch.object(bc, "get_template_name_from_intent", return_value="product_render"), \
             patch.object(bc, "extract_product_render_intent", return_value=extraction_result), \
             patch.object(bc, "build_product_render_scene_script", side_effect=_builder_explodes), \
             patch.object(bc, "generate_with_ollama", return_value="```python\nimport bpy\n```"):
            req = bc.build_blender_script(
                message="bouteille amber glass déclenche un bug builder",
                context={},
                request_id="t-h53-bldr-exc",
            )

        # Le pipeline ne crashe pas et retombe sur legacy
        assert req.pipeline_path == "legacy_llm_bpy_scaffold"
        assert req.product_render_intent is None

    def test_feature_flag_disabled_forces_legacy_path_even_for_product_render(
        self, tmp_path, monkeypatch
    ):
        """Garde-fou : si BLENDER_USE_PRODUCT_RENDER_IR=False, l'extracteur
        n'est jamais appelé même quand template_used == product_render."""
        from app.clients import blender_client as bc

        monkeypatch.setattr(bc, "BLENDER_OUTPUT_DIR", str(tmp_path))
        monkeypatch.setattr(bc, "BLENDER_USE_PRODUCT_RENDER_IR", False)

        fake_intent = self._setup_basic_mocks(tmp_path)
        extractor_calls = {"count": 0}

        def _track_extractor(*args, **kwargs):
            extractor_calls["count"] += 1
            raise AssertionError("extractor must NOT be called when flag is off")

        with patch.object(bc, "parse_artistic_intent", return_value=fake_intent), \
             patch.object(bc, "write_intent_json"), \
             patch.object(bc, "select_template_from_intent", return_value="<scaffold-stub>"), \
             patch.object(bc, "get_template_name_from_intent", return_value="product_render"), \
             patch.object(bc, "extract_product_render_intent", side_effect=_track_extractor), \
             patch.object(bc, "generate_with_ollama", return_value="```python\nimport bpy\n```"):
            req = bc.build_blender_script(
                message="anything", context={}, request_id="t-h53-flag-off",
            )

        assert req.pipeline_path == "legacy_llm_bpy_scaffold"
        assert req.product_render_intent is None
        assert extractor_calls["count"] == 0


# ---------------------------------------------------------------------------
# H.5.3 — Constantes de traçabilité du chemin
# ---------------------------------------------------------------------------

def test_pipeline_path_constants_are_stable_strings():
    """Garde-fou : ces constantes apparaissent dans manifest.json.future.
    Tout renommage est cassant pour les consommateurs en aval."""
    from app.clients.blender_client import (
        PIPELINE_PATH_BUILDER,
        PIPELINE_PATH_LEGACY,
    )
    assert PIPELINE_PATH_BUILDER == "product_render_ir_builder"
    assert PIPELINE_PATH_LEGACY == "legacy_llm_bpy_scaffold"


# ---------------------------------------------------------------------------
# H.6.11 — preview_fidelity_v1 : lisibilité verre/métal dans la preview EEVEE
# Tous les réglages sont transitoires dans le subprocess preview ; scene.blend
# n'est jamais réécrit. APIs validées contre Blender 5.1.1 (EEVEE Next).
# ---------------------------------------------------------------------------

def test_render_preview_script_compiles(tmp_path):
    """Garde-fou H.6.11 : la chaîne Python générée doit être syntaxiquement valide.
    Les assertions textuelles verrouillent le contrat mais ne détectent pas un
    script invalide ; compile(mode='exec') attrape ça."""
    script = _capture_render_script(tmp_path)
    compile(script, "render_preview.py", "exec")


def test_render_preview_enables_scene_raytracing(tmp_path):
    """Le ray tracing scène (requis verre+métal en EEVEE Next) est activé sous garde hasattr."""
    script = _capture_render_script(tmp_path)
    assert 'hasattr(_pf_scene.eevee, "use_raytracing")' in script
    assert "_pf_scene.eevee.use_raytracing = True" in script


def test_render_preview_refraction_reads_transmission_weight(tmp_path):
    """La détection lit l'input exact 5.1.1 'Transmission Weight', repli 'Transmission'."""
    script = _capture_render_script(tmp_path)
    assert '"Transmission Weight"' in script
    assert '"Transmission"' in script


def test_render_preview_refraction_gated_by_transmission_threshold(tmp_path):
    """La réfraction n'est activée que sur matériaux à transmission > 0 (verre/translucent)."""
    script = _capture_render_script(tmp_path)
    assert "bpy.data.materials" in script
    assert "default_value > 0.0" in script


def test_render_preview_uses_raytrace_refraction_primary(tmp_path):
    """Flag primaire confirmé par introspection = use_raytrace_refraction (EEVEE Next)."""
    script = _capture_render_script(tmp_path)
    assert "use_raytrace_refraction = True" in script


def test_render_preview_screen_refraction_is_conditional_fallback(tmp_path):
    """use_screen_refraction n'est qu'un repli défensif (elif hasattr), jamais écrit en plus."""
    script = _capture_render_script(tmp_path)
    assert 'elif hasattr(_pf_mat, "use_screen_refraction")' in script
    # le primaire est sous if, le repli sous elif : jamais les deux inconditionnellement
    assert script.count("use_raytrace_refraction = True") == 1


def test_render_preview_uses_directional_world_gradient(tmp_path):
    """Environnement procédural directionnel world-space (Geometry.Incoming),
    pas un effet écran/caméra-dépendant, et plus le fond plat (0.05,...)."""
    script = _capture_render_script(tmp_path)
    assert "ShaderNodeNewGeometry" in script
    assert '"Incoming"' in script
    assert "(0.05, 0.05, 0.05, 1.0)" not in script


def test_render_preview_world_gradient_is_bounded_and_neutral(tmp_path):
    """Le gradient reste discret/neutre et borné (0.03→0.12) pour préserver l'exposition H.6.9."""
    script = _capture_render_script(tmp_path)
    assert "(0.03, 0.03, 0.03, 1.0)" in script
    assert "(0.12, 0.12, 0.12, 1.0)" in script


def test_render_preview_does_not_save_blend(tmp_path):
    """Invariant : le script preview ne sauvegarde JAMAIS scene.blend."""
    script = _capture_render_script(tmp_path)
    assert "save_as_mainfile" not in script
    assert "wm.save" not in script


def test_render_preview_does_not_touch_protected_settings(tmp_path):
    """H.6.11 ne touche ni Fast GI, ni les lumières/exposition, ni l'échantillonnage."""
    script = _capture_render_script(tmp_path)
    assert "use_fast_gi" not in script
    assert "taa_render_samples" not in script
    # pas de modification des énergies de lumière (exposition H.6.9 préservée)
    assert "energy" not in script
