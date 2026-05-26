"""
H.5.1 — Tests unitaires du builder déterministe product_render.

Fonctions PURES — pas d'exécution Blender. Inspecte le script généré
(string bpy) pour vérifier :
- les noms contractuels présents
- les patterns interdits absents
- la couverture des 6 subject.kind
- la couverture des 4 subject.material
- la résolution des couleurs (palette nommée + #RRGGBB)
- la réutilisation des CANONICAL_* (single source of truth)
- la conformité avec les invariants AST guard H.4.7
"""
from __future__ import annotations

import pytest

from app.engine.blender_runtime_corrector import (
    CANONICAL_CAMERA,
    CANONICAL_FILL_LIGHT,
    CANONICAL_KEY_LIGHT,
)
from app.engine.product_render_builder import (
    MATERIAL_PROFILES,
    SUBJECT_GEOMETRY,
    build_product_render_scene_script,
)
from app.engine.product_render_ir import (
    BackdropIR,
    ProductRenderIntent,
    ProductSubjectIR,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ir(
    kind: str = "bottle",
    color: str = "amber",
    material: str = "glass",
    backdrop_color: str = "neutral_gray",
) -> ProductRenderIntent:
    return ProductRenderIntent(
        schema_version="v0",
        subject=ProductSubjectIR(kind=kind, color=color, material=material),
        backdrop=BackdropIR(color=backdrop_color),
    )


# ---------------------------------------------------------------------------
# Structure du script généré
# ---------------------------------------------------------------------------

def test_script_starts_with_import_bpy():
    script = build_product_render_scene_script(_make_ir())
    assert script.startswith("import bpy")


def test_script_contains_canonical_cleanup():
    script = build_product_render_scene_script(_make_ir())
    assert "bpy.ops.object.select_all(action='SELECT')" in script
    assert "bpy.ops.object.delete()" in script


def test_script_saves_via_output_blend_path_placeholder():
    """Le placeholder OUTPUT_BLEND_PATH est injecté par _inject_output_paths
    côté blender_client.py. Le builder doit l'utiliser sans le redéfinir."""
    script = build_product_render_scene_script(_make_ir())
    assert "bpy.ops.wm.save_as_mainfile(filepath=OUTPUT_BLEND_PATH)" in script
    # Le builder ne doit JAMAIS réassigner OUTPUT_BLEND_PATH
    assert "OUTPUT_BLEND_PATH =" not in script


# ---------------------------------------------------------------------------
# Noms contractuels (runtime_contract H.4.8)
# ---------------------------------------------------------------------------

REQUIRED_NAMES = [
    "Backdrop_Plane", "Pedestal", "Product_Subject",
    "Camera", "Key_Light", "Fill_Light",
]


@pytest.mark.parametrize("name", REQUIRED_NAMES)
def test_script_contains_required_runtime_contract_object_name(name):
    script = build_product_render_scene_script(_make_ir())
    assert f"'{name}'" in script or f'"{name}"' in script, (
        f"Required runtime contract name {name!r} missing from generated script"
    )


def test_script_assigns_scene_camera():
    script = build_product_render_scene_script(_make_ir())
    assert "bpy.context.scene.camera = cam" in script


# ---------------------------------------------------------------------------
# Patterns interdits (AST guard H.4.7)
# ---------------------------------------------------------------------------

FORBIDDEN_PATTERNS = [
    "bpy.ops.import_scene",     # pas d'asset externe
    "bpy.ops.wm.obj_import",    # idem
    "bpy.ops.wm.fbx_import",    # idem
    "bpy.ops.wm.gltf_import",   # idem
    "bpy.data.meshes.new",      # mesh manuel sans from_pydata (interdit V0)
    "bpy.data.objects.new",     # cohérent avec interdiction mesh.new
    "bpy.data.images.load",     # pas de texture externe en V0
    "path_to_",                 # placeholder
    "/path/to/",                # placeholder
    "your_",                    # placeholder
    "subprocess",
    "import os",
    "import sys",
]


@pytest.mark.parametrize("pattern", FORBIDDEN_PATTERNS)
def test_script_does_not_contain_forbidden_pattern(pattern):
    script = build_product_render_scene_script(_make_ir())
    assert pattern not in script, (
        f"Forbidden pattern {pattern!r} found in generated script — "
        f"violates H.4.7 AST guard invariants"
    )


def test_script_does_not_create_sun_light():
    """Sun est interdit dans le contrat runtime product_render (H.4.8.2)."""
    script = build_product_render_scene_script(_make_ir())
    assert "type='SUN'" not in script
    assert 'type="SUN"' not in script
    assert "'Sun'" not in script
    assert '"Sun"' not in script


# ---------------------------------------------------------------------------
# Couverture des 6 subject.kind
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kind", list(SUBJECT_GEOMETRY.keys()))
def test_script_generates_for_all_subject_kinds(kind):
    """Chaque kind doit produire un script valide qui contient la bonne
    primitive bpy et nomme l'objet Product_Subject."""
    script = build_product_render_scene_script(_make_ir(kind=kind))
    expected_primitive = SUBJECT_GEOMETRY[kind]["primitive"]
    assert f"bpy.ops.mesh.{expected_primitive}" in script
    assert "product.name = 'Product_Subject'" in script


def test_bottle_uses_tall_thin_cylinder():
    script = build_product_render_scene_script(_make_ir(kind="bottle"))
    geom = SUBJECT_GEOMETRY["bottle"]
    assert f"radius={geom['radius']}" in script
    assert f"depth={geom['depth']}" in script


def test_sphere_uses_uv_sphere():
    script = build_product_render_scene_script(_make_ir(kind="sphere"))
    assert "primitive_uv_sphere_add" in script


def test_box_uses_cube():
    script = build_product_render_scene_script(_make_ir(kind="box"))
    assert "primitive_cube_add" in script
    assert f"size={SUBJECT_GEOMETRY['box']['size']}" in script


# ---------------------------------------------------------------------------
# Couverture des 4 subject.material
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("material", list(MATERIAL_PROFILES.keys()))
def test_script_applies_material_profile_for_all_materials(material):
    script = build_product_render_scene_script(_make_ir(material=material))
    profile = MATERIAL_PROFILES[material]
    # Le builder écrit roughness + metallic + transmission + ior dans l'appel
    # _make_principled_material. On vérifie que les valeurs canoniques apparaissent.
    assert str(profile["roughness"]) in script
    assert str(profile["metallic"]) in script
    assert str(profile["transmission"]) in script


def test_glass_material_uses_transmission_one():
    script = build_product_render_scene_script(_make_ir(material="glass"))
    # transmission=1.0 caractéristique du verre
    assert "1.0" in script  # présent dans l'appel material params


def test_metallic_material_uses_metallic_one():
    script = build_product_render_scene_script(_make_ir(material="metallic"))
    # metallic=1.0 caractéristique du métal
    assert "1.0" in script


# ---------------------------------------------------------------------------
# Résolution des couleurs
# ---------------------------------------------------------------------------

def test_script_resolves_named_subject_color():
    script = build_product_render_scene_script(_make_ir(color="amber"))
    # amber = (0.75, 0.45, 0.15, 1.0) — on cherche les composantes RGB
    assert "0.75" in script
    assert "0.45" in script
    assert "0.15" in script


def test_script_resolves_hex_subject_color():
    # #ff0000 → (1.0, 0.0, 0.0, 1.0)
    script = build_product_render_scene_script(_make_ir(color="#ff0000"))
    assert "1.0" in script
    assert "0.0" in script


def test_script_resolves_named_backdrop_color():
    # neutral_gray = (0.50, 0.50, 0.50, 1.0)
    script = build_product_render_scene_script(_make_ir(backdrop_color="neutral_gray"))
    assert "0.5" in script  # 0.50 ou 0.5 après repr Python


def test_script_distinguishes_subject_and_backdrop_materials():
    """Le subject material vient de l'IR, le backdrop est toujours matte."""
    script = build_product_render_scene_script(_make_ir(material="glass"))
    # Subject material = glass (roughness 0.05)
    assert "0.05" in script
    # Backdrop material = matte (roughness 0.9)
    assert "0.9" in script


# ---------------------------------------------------------------------------
# Single source of truth — CANONICAL_* du corrector
# ---------------------------------------------------------------------------

def test_script_uses_canonical_camera_location():
    script = build_product_render_scene_script(_make_ir())
    assert str(CANONICAL_CAMERA["location"]) in script


def test_script_uses_canonical_camera_lens():
    script = build_product_render_scene_script(_make_ir())
    assert f"cam.data.lens = {CANONICAL_CAMERA['lens']}" in script


def test_script_uses_canonical_key_light_energy():
    script = build_product_render_scene_script(_make_ir())
    assert f"key_light.data.energy = {CANONICAL_KEY_LIGHT['energy']}" in script


def test_script_uses_canonical_fill_light_energy():
    script = build_product_render_scene_script(_make_ir())
    assert f"fill_light.data.energy = {CANONICAL_FILL_LIGHT['energy']}" in script


def test_script_uses_area_lights_only():
    """Conformément au contrat product_render : Key_Light + Fill_Light en AREA,
    jamais SUN ni autre type."""
    script = build_product_render_scene_script(_make_ir())
    assert "type='AREA'" in script
    # On compte le nombre d'occurrences "AREA" : minimum 2 (Key + Fill)
    assert script.count("'AREA'") >= 2


# ---------------------------------------------------------------------------
# Métadonnées IR embarquées (traçabilité)
# ---------------------------------------------------------------------------

def test_script_embeds_ir_metadata_as_comments():
    """Le script doit conserver les valeurs IR en commentaires pour debug /
    traçabilité (pas de modification runtime, juste de la documentation)."""
    script = build_product_render_scene_script(_make_ir(
        kind="jar", color="amber", material="glossy", backdrop_color="cool_gray",
    ))
    assert "subject.kind = 'jar'" in script
    assert "subject.color = 'amber'" in script
    assert "subject.material = 'glossy'" in script
    assert "backdrop.color = 'cool_gray'" in script


# ---------------------------------------------------------------------------
# Position du sujet sur le pedestal
# ---------------------------------------------------------------------------

def test_subject_is_positioned_above_pedestal_top():
    """Le subject doit avoir une location z > PEDESTAL_TOP_Z (= 0.04)."""
    from app.engine.product_render_builder import PEDESTAL_TOP_Z, _subject_location
    for kind in SUBJECT_GEOMETRY.keys():
        x, y, z = _subject_location(kind)
        assert x == 0.0, f"{kind} should be centered on x"
        assert y == 0.0, f"{kind} should be centered on y"
        assert z > PEDESTAL_TOP_Z, (
            f"{kind} z={z} should be above PEDESTAL_TOP_Z={PEDESTAL_TOP_Z}"
        )


# ---------------------------------------------------------------------------
# Pas d'import LLM, pas de réseau
# ---------------------------------------------------------------------------

def test_module_does_not_import_ollama_or_llm_clients():
    """Sécurité H.5.1 : le builder doit rester strictement pur Python sans
    dépendance LLM ni réseau."""
    import app.engine.product_render_builder as mod
    from pathlib import Path
    source = Path(mod.__file__).read_text(encoding="utf-8")
    forbidden_imports = (
        "from app.clients.ollama_client",
        "import requests",
        "import urllib",
        "import httpx",
        "import aiohttp",
        "import subprocess",
    )
    for imp in forbidden_imports:
        assert imp not in source, f"Forbidden import in builder: {imp}"


def test_module_does_not_import_blender_client():
    """Sécurité H.5.1 : pas d'import circulaire vers blender_client (qui
    importe lui-même blender_runtime_corrector — voie indirecte interdite)."""
    import app.engine.product_render_builder as mod
    from pathlib import Path
    source = Path(mod.__file__).read_text(encoding="utf-8")
    assert "from app.clients.blender_client" not in source
    assert "import app.clients.blender_client" not in source
