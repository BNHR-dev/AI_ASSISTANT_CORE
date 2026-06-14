"""
H.6.11 preview_fidelity_v1 — tests de la politique de fidélité PARTAGÉE et de
sa PARITÉ entre les deux chemins qui écrivent preview.png :
  - app.clients.blender_client._render_preview      (chemin générique / legacy)
  - app.engine.blender_runtime_corrector            (dernier writer product_render)

La source unique est app.engine.blender_preview_fidelity. Les tests de parité
empêchent toute divergence future entre les deux chemins.
"""
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.engine.blender_preview_fidelity import (
    PREVIEW_ENV_HIGH,
    PREVIEW_ENV_LOW,
    preview_fidelity_script_block,
    preview_fidelity_script_lines,
)
from app.engine.blender_runtime_corrector import (
    CORRECTION_NORMALIZE_CAMERA,
    CORRECTION_NORMALIZE_LIGHTING,
    CORRECTION_RERENDER_PREVIEW,
    build_correction_script,
)
from app.clients.blender_client import _render_preview
from app.engine.blender_types import BlenderRequest

# Sentinelle : première ligne du bloc partagé. Sa présence dans un script généré
# prouve que la politique partagée y est injectée.
SHARED_SENTINEL = preview_fidelity_script_lines()[0]


# ---------------------------------------------------------------------------
# Politique partagée (source unique)
# ---------------------------------------------------------------------------

def test_shared_block_compiles():
    """Garde-fou : le bloc partagé doit être syntaxiquement valide."""
    compile(preview_fidelity_script_block(), "preview_fidelity_block.py", "exec")


def test_raytracing_under_defensive_guard():
    block = preview_fidelity_script_block()
    assert 'hasattr(_pf_scene.eevee, "use_raytracing")' in block
    assert "_pf_scene.eevee.use_raytracing = True" in block


def test_refraction_reads_transmission_weight_with_fallback():
    block = preview_fidelity_script_block()
    assert '"Transmission Weight"' in block
    assert '"Transmission"' in block


def test_refraction_gated_by_threshold():
    block = preview_fidelity_script_block()
    assert "bpy.data.materials" in block
    assert "default_value > 0.0" in block


def test_refraction_primary_flag_is_raytrace():
    block = preview_fidelity_script_block()
    assert "_pf_mat.use_raytrace_refraction = True" in block
    # un seul write du flag primaire (pas d'écriture aveugle multiple)
    assert block.count("use_raytrace_refraction = True") == 1


def test_screen_refraction_is_conditional_fallback_only():
    block = preview_fidelity_script_block()
    assert 'elif hasattr(_pf_mat, "use_screen_refraction")' in block


def test_environment_is_directional_world_space():
    """Geometry.Incoming (world-space), pas un effet écran/caméra-dépendant."""
    block = preview_fidelity_script_block()
    assert "ShaderNodeNewGeometry" in block
    assert '"Incoming"' in block


def test_environment_is_bounded_and_neutral():
    block = preview_fidelity_script_block()
    assert str(PREVIEW_ENV_LOW) in block
    assert str(PREVIEW_ENV_HIGH) in block
    assert PREVIEW_ENV_LOW == (0.03, 0.03, 0.03, 1.0)
    assert PREVIEW_ENV_HIGH == (0.12, 0.12, 0.12, 1.0)
    # plus jamais l'ancien fond plat 0.05
    assert "(0.05, 0.05, 0.05, 1.0)" not in block


def test_shared_block_does_not_touch_protected_concerns():
    """La politique ne touche NI caméra, NI lumières/exposition, NI Fast GI,
    NI résolution/échantillonnage, et ne sauvegarde jamais le .blend."""
    block = preview_fidelity_script_block()
    for forbidden in (
        "save_as_mainfile", "wm.save",
        "light_add", "energy", "camera", "lens",
        "use_fast_gi", "taa_render_samples", "use_shadows",
        "resolution_x", "render.render",
    ):
        assert forbidden not in block, f"token interdit dans la politique : {forbidden}"


# ---------------------------------------------------------------------------
# Parité — chemin 2 : runtime corrector (dernier writer product_render)
# ---------------------------------------------------------------------------

def _corrector_rerender_script() -> str:
    return build_correction_script(
        "/tmp/scene.blend", "/tmp/preview.png",
        [CORRECTION_NORMALIZE_LIGHTING, CORRECTION_NORMALIZE_CAMERA,
         CORRECTION_RERENDER_PREVIEW],
    )


def test_corrector_rerender_injects_shared_policy():
    script = _corrector_rerender_script()
    assert SHARED_SENTINEL in script
    assert "use_raytracing = True" in script
    assert "use_raytrace_refraction = True" in script
    assert "ShaderNodeNewGeometry" in script


def test_corrector_rerender_drops_old_flat_world():
    """L'ancien fond plat (0.05,...) ne doit plus apparaître dans le re-rendu."""
    script = _corrector_rerender_script()
    assert "(0.05, 0.05, 0.05, 1.0)" not in script


def test_corrector_rerender_still_compiles():
    compile(_corrector_rerender_script(), "correction_script.py", "exec")


def test_corrector_fidelity_preserves_camera_and_lights():
    """Invariant : la fidélité n'enlève pas la caméra canonique ni les lumières
    déjà appliquées par le corrector (exposition/cadrage intacts)."""
    script = _corrector_rerender_script()
    assert "scene.camera = _cam" in script         # caméra canonique conservée
    assert 'bpy.data.objects.get("Key_Light")' in script
    assert "render.render(write_still=True)" in script


# ---------------------------------------------------------------------------
# Parité — chemin 1 : _render_preview (blender_client)
# ---------------------------------------------------------------------------

def _capture_render_preview_script(tmp_path) -> str:
    output_path = str(tmp_path / "scene.blend")
    Path(output_path).write_bytes(b"BLEND")
    request = BlenderRequest(
        request_id="t", script_content="import bpy",
        script_path=str(tmp_path / "scene.py"),
        output_path=output_path, render_path=str(tmp_path / "preview.png"),
        output_dir=str(tmp_path), timeout=10,
    )
    captured = {}

    def fake_subprocess(cmd, **kwargs):
        sp = tmp_path / "render_preview.py"
        if sp.exists():
            captured["script"] = sp.read_text(encoding="utf-8")
        return MagicMock(returncode=0)

    with patch("subprocess.run", side_effect=fake_subprocess):
        _render_preview("/usr/bin/blender", request)
    return captured.get("script", "")


def test_render_preview_injects_shared_policy(tmp_path):
    script = _capture_render_preview_script(tmp_path)
    assert SHARED_SENTINEL in script


def test_both_paths_share_identical_policy(tmp_path):
    """Anti-divergence : les deux chemins injectent le MÊME bloc partagé."""
    preview_script = _capture_render_preview_script(tmp_path)
    corrector_script = _corrector_rerender_script()
    block = preview_fidelity_script_block()
    assert block in preview_script
    assert block in corrector_script
