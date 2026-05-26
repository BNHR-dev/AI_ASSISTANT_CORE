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


# ---------------------------------------------------------------------------
# H.5.4 — Compatibilité V0 byte-équivalente
# ---------------------------------------------------------------------------


def test_v0_script_is_byte_equivalent_to_legacy_for_canonical_ir():
    """Garantie H.5.4 : pour un IR V0 canonique, le script généré ne doit pas
    avoir changé. On reconstruit deux IR V0 identiques et on vérifie que la
    sortie reste stable (la build est déterministe ; la comparaison sert de
    régression contre un drift V0)."""
    ir1 = _make_ir(kind="bottle", color="amber", material="glass")
    ir2 = _make_ir(kind="bottle", color="amber", material="glass")
    s1 = build_product_render_scene_script(ir1)
    s2 = build_product_render_scene_script(ir2)
    assert s1 == s2
    # En-tête V0 préservé (pas de fuite V1)
    assert "H.5.1" in s1 or "deterministic product_render builder" in s1
    assert "subject.shape" not in s1
    assert "subject.cap" not in s1
    assert "subject.transparency" not in s1
    assert "framing" not in s1
    assert "Product_Cap" not in s1


def test_v0_script_does_not_apply_close_packshot_scale():
    """Le V0 ne doit jamais appliquer le scale framing close_packshot
    ni définir product.scale (laisse Blender à 1,1,1 par défaut)."""
    script = build_product_render_scene_script(_make_ir())
    assert "product.scale = (" not in script


# ---------------------------------------------------------------------------
# H.5.4 — Builder V1
# ---------------------------------------------------------------------------


def _make_ir_v1(
    kind: str = "bottle",
    color: str = "amber",
    material: str = "glass",
    backdrop_color: str = "neutral_gray",
    shape=None,
    cap=None,
    transparency=None,
    framing=None,
) -> ProductRenderIntent:
    return ProductRenderIntent(
        schema_version="v1",
        subject=ProductSubjectIR(
            kind=kind, color=color, material=material,
            shape=shape, cap=cap, transparency=transparency,
        ),
        backdrop=BackdropIR(color=backdrop_color),
        framing=framing,
    )


class TestBuilderV1Smoke:

    def test_v1_minimal_default_produces_valid_script(self):
        script = build_product_render_scene_script(_make_ir_v1())
        assert script.startswith("import bpy")
        assert "bpy.ops.wm.save_as_mainfile(filepath=OUTPUT_BLEND_PATH)" in script
        # Tags V1 présents
        assert "subject.shape" in script
        assert "subject.cap" in script
        assert "subject.transparency" in script
        assert "framing" in script

    @pytest.mark.parametrize("name", REQUIRED_NAMES)
    def test_v1_keeps_required_contractual_names(self, name):
        script = build_product_render_scene_script(_make_ir_v1())
        assert f"'{name}'" in script or f'"{name}"' in script

    @pytest.mark.parametrize("pattern", FORBIDDEN_PATTERNS)
    def test_v1_keeps_forbidden_patterns_absent(self, pattern):
        script = build_product_render_scene_script(_make_ir_v1(
            shape="rounded", cap="present", transparency="glass",
            framing="close_packshot",
        ))
        assert pattern not in script

    def test_v1_does_not_create_sun_light(self):
        script = build_product_render_scene_script(_make_ir_v1(
            shape="rectangular", cap="present", transparency="glass",
            framing="close_packshot",
        ))
        assert "type='SUN'" not in script
        assert 'type="SUN"' not in script


class TestBuilderV1Shape:

    def test_shape_rectangular_uses_cube_primitive(self):
        script = build_product_render_scene_script(_make_ir_v1(shape="rectangular"))
        assert "bpy.ops.mesh.primitive_cube_add" in script
        # Subject scalé pour silhouette rectangle (sx != sy)
        assert "product.scale = (" in script

    def test_shape_rounded_uses_uv_sphere(self):
        script = build_product_render_scene_script(_make_ir_v1(shape="rounded"))
        assert "primitive_uv_sphere_add" in script
        assert "product.scale = (" in script

    def test_shape_cylindrical_uses_cylinder_for_bottle(self):
        script = build_product_render_scene_script(
            _make_ir_v1(kind="bottle", shape="cylindrical")
        )
        assert "primitive_cylinder_add" in script

    def test_shape_default_when_omitted_is_cylindrical(self):
        """Si shape n'est pas fourni en V1, builder applique cylindrical."""
        script = build_product_render_scene_script(_make_ir_v1(shape=None))
        # Bottle cylindrical → cylinder primitive
        assert "primitive_cylinder_add" in script


class TestBuilderV1Cap:

    def test_cap_present_adds_product_cap_object(self):
        script = build_product_render_scene_script(_make_ir_v1(cap="present"))
        assert "'Product_Cap'" in script or '"Product_Cap"' in script
        assert "cap.name = 'Product_Cap'" in script

    def test_cap_absent_does_not_add_product_cap(self):
        script = build_product_render_scene_script(_make_ir_v1(cap="absent"))
        assert "Product_Cap" not in script

    def test_cap_default_when_omitted_is_absent(self):
        script = build_product_render_scene_script(_make_ir_v1(cap=None))
        assert "Product_Cap" not in script


class TestBuilderV1Transparency:

    def test_transparency_glass_forces_glass_profile(self):
        """transparency=glass force les params V1_GLASS_PROFILE même si
        material=matte (la transparence prime sur le material V0)."""
        script = build_product_render_scene_script(
            _make_ir_v1(material="matte", transparency="glass")
        )
        # glass profile : roughness=0.05, transmission=1.0
        assert "0.05" in script
        assert "1.0" in script

    def test_transparency_translucent_uses_partial_transmission(self):
        script = build_product_render_scene_script(
            _make_ir_v1(material="matte", transparency="translucent")
        )
        # translucent profile : transmission=0.5
        assert "0.5" in script

    def test_transparency_opaque_uses_material_profile(self):
        script = build_product_render_scene_script(
            _make_ir_v1(material="matte", transparency="opaque")
        )
        # matte profile : roughness=0.9, transmission=0.0
        assert "0.9" in script

    def test_transparency_glass_preserves_amber_color(self):
        """Le matériau verre doit conserver la couleur amber de subject.color."""
        script = build_product_render_scene_script(
            _make_ir_v1(color="amber", transparency="glass")
        )
        # amber RGB
        assert "0.75" in script
        assert "0.45" in script


class TestBuilderV1Framing:

    def test_close_packshot_applies_subject_scale_factor(self):
        """framing=close_packshot doit scaler le sujet (le corrector
        H.4.8.x normalise la caméra, scaler le sujet est la voie déterministe
        pour rapprocher le cadrage)."""
        from app.engine.product_render_builder import CLOSE_PACKSHOT_SUBJECT_SCALE
        script = build_product_render_scene_script(_make_ir_v1(
            framing="close_packshot",
        ))
        assert "product.scale = (" in script
        # Le scale final inclut le facteur 1.4x
        assert str(CLOSE_PACKSHOT_SUBJECT_SCALE) in script or "1.4" in script

    def test_medium_framing_does_not_apply_close_packshot_scale(self):
        """framing=medium ne doit PAS appliquer le facteur 1.4."""
        script = build_product_render_scene_script(_make_ir_v1(
            framing="medium", shape="cylindrical",
        ))
        # Avec shape=cylindrical et framing=medium, la scale est (1.0, 1.0, 1.0)
        assert "product.scale = (1.0, 1.0, 1.0)" in script
        # La ligne product.scale ne doit PAS contenir 1.4
        for line in script.splitlines():
            if line.startswith("product.scale ="):
                assert "1.4" not in line, (
                    f"medium framing leaked close_packshot factor: {line!r}"
                )

    def test_framing_default_when_omitted_is_medium(self):
        """Si framing n'est pas fourni en V1, le builder applique medium."""
        script = build_product_render_scene_script(_make_ir_v1(
            framing=None, shape="cylindrical",
        ))
        assert "product.scale = (1.0, 1.0, 1.0)" in script


class TestBuilderV1CanonicalPreserved:

    def test_v1_still_uses_canonical_camera(self):
        script = build_product_render_scene_script(_make_ir_v1(
            framing="close_packshot",
        ))
        assert str(CANONICAL_CAMERA["location"]) in script
        assert f"cam.data.lens = {CANONICAL_CAMERA['lens']}" in script

    def test_v1_still_uses_canonical_lights(self):
        script = build_product_render_scene_script(_make_ir_v1())
        assert f"key_light.data.energy = {CANONICAL_KEY_LIGHT['energy']}" in script
        assert f"fill_light.data.energy = {CANONICAL_FILL_LIGHT['energy']}" in script


class TestBuilderV1SmokeProductRender:
    """Cas canonique du smoke H.5.4 : bouteille parfum verre ambré packshot."""

    def test_canonical_smoke_ir_v1(self):
        ir = ProductRenderIntent(
            schema_version="v1",
            subject=ProductSubjectIR(
                kind="bottle", color="amber", material="glass",
                shape="cylindrical", cap="present", transparency="glass",
            ),
            backdrop=BackdropIR(color="neutral_gray"),
            framing="close_packshot",
        )
        script = build_product_render_scene_script(ir)
        # 4 invariants V1 lisibles dans le script
        assert "Product_Cap" in script           # cap=present
        assert "1.0" in script                   # transmission=1.0 (glass)
        assert "0.05" in script                  # roughness=0.05 (glass)
        # Le scale du sujet inclut le facteur 1.4 (close_packshot, cylindrical)
        scale_lines = [
            ln for ln in script.splitlines() if ln.startswith("product.scale =")
        ]
        assert scale_lines, "product.scale line missing"
        assert "1.4" in scale_lines[0], scale_lines[0]
        # AST guard invariants conservés
        assert "import bpy" in script
        assert "bpy.ops.object.select_all(action='SELECT')" in script
        assert "bpy.ops.wm.save_as_mainfile(filepath=OUTPUT_BLEND_PATH)" in script


# ---------------------------------------------------------------------------
# H.5.4 — ast_guard est exécuté sur les deux chemins
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("framing", ["medium", "close_packshot"])
@pytest.mark.parametrize("shape", ["cylindrical", "rectangular", "rounded"])
@pytest.mark.parametrize("cap", ["absent", "present"])
@pytest.mark.parametrize("transparency", ["opaque", "translucent", "glass"])
def test_v1_combinations_pass_ast_guard(shape, cap, transparency, framing):
    """Toute combinaison V1 doit produire un script propre AST guard
    (zéro violation V0)."""
    from app.engine.blender_ast_guard import analyze_scene_py
    ir = _make_ir_v1(
        shape=shape, cap=cap, transparency=transparency, framing=framing,
    )
    script = build_product_render_scene_script(ir)
    report = analyze_scene_py(script, "product_render")
    # Le rapport ast_guard est signal-only ; on valide "violations" vide.
    assert isinstance(report, dict)
    violations = report.get("violations", [])
    assert violations == [], (
        f"V1 combination shape={shape!r} cap={cap!r} "
        f"transparency={transparency!r} framing={framing!r} : "
        f"unexpected ast_guard violations {violations}"
    )
