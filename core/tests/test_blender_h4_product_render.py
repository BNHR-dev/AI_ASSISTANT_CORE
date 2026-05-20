"""
Tests — H.4.2 : ajout du template Blender contrôlé `product_render`.

Vérifie :
- select_template_from_intent() : product_render via ArtisticIntent ET dict
- get_template_name_from_intent() : "product_render" quand sélectionné
- Sujets compatibles : bouteille, flacon, parfum, mockup, maquette, packaging…
- Sujet incompatible avec medium=product_render → None (règle conservatrice)
- Fallback message brut : "bouteille de parfum", "mockup produit", "packaging",
  "packshot produit", "rendu produit" → product_render
- "studio" seul NE déclenche PAS product_render
- interior_space reste intact
- Prompt intérieur reste interior_space
- Prompt neutre reste None
- build_blender_script() renseigne request.template_used = "product_render"
- manifest.future.template_used == "product_render" pour un prompt produit
- Intégrité du scaffold : Camera, Key_Light (AREA), OUTPUT_BLEND_PATH, pas de Wall_
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.clients.blender_client import build_blender_script
from app.engine.artifact_manifest import build_blender_manifest
from app.engine.artistic_intent import ArtisticIntent
from app.engine.blender_templates import (
    TEMPLATE_INTERIOR_SPACE,
    TEMPLATE_PRODUCT_RENDER,
    get_template_name,
    get_template_name_from_intent,
    select_template,
    select_template_from_intent,
)
from app.engine.blender_types import BlenderRequest, BlenderResult


_FAKE_ID = "test-h4-product-001"
_FAKE_DIR = f"outputs/blender/{_FAKE_ID}"


# ---------------------------------------------------------------------------
# select_template_from_intent — product_render via intent structuré
# ---------------------------------------------------------------------------

class TestSelectProductRenderFromIntent:

    def test_product_render_via_artistic_intent(self):
        intent = ArtisticIntent(medium="product_render", subject_main="bouteille")
        assert select_template_from_intent(intent) is TEMPLATE_PRODUCT_RENDER
        assert get_template_name_from_intent(intent) == "product_render"

    def test_product_render_via_dict_intent(self):
        intent = {"medium": "product_render", "subject_main": "bouteille"}
        assert select_template_from_intent(intent) is TEMPLATE_PRODUCT_RENDER
        assert get_template_name_from_intent(intent) == "product_render"

    @pytest.mark.parametrize("subject", [
        "bouteille", "flacon", "parfum",
        "produit", "product",
        "mockup", "maquette", "packaging", "packshot",
        "cube", "sphère", "sphere",
    ])
    def test_product_subjects_match(self, subject):
        intent = ArtisticIntent(medium="product_render", subject_main=subject)
        assert get_template_name_from_intent(intent) == "product_render"

    def test_product_render_with_incompatible_subject_returns_none(self):
        """Règle conservatrice : medium=product_render mais sujet 'laboratoire' → None."""
        intent = ArtisticIntent(medium="product_render", subject_main="laboratoire")
        assert select_template_from_intent(intent) is None
        assert get_template_name_from_intent(intent) is None

    def test_product_render_with_unknown_subject_returns_none(self):
        intent = ArtisticIntent(medium="product_render", subject_main="unknown")
        assert select_template_from_intent(intent) is None


# ---------------------------------------------------------------------------
# Non-régression — interior_space reste fonctionnel
# ---------------------------------------------------------------------------

class TestInteriorSpaceStillWorks:

    def test_interior_intent_still_returns_interior_space(self):
        intent = ArtisticIntent(medium="3d_scene", subject_main="laboratoire")
        assert select_template_from_intent(intent) is TEMPLATE_INTERIOR_SPACE
        assert get_template_name_from_intent(intent) == "interior_space"

    def test_interior_message_still_returns_interior_space(self):
        assert get_template_name("crée un bureau simple") == "interior_space"
        assert select_template("crée un bureau simple") is TEMPLATE_INTERIOR_SPACE

    def test_neutral_message_still_returns_none(self):
        assert select_template("crée une sphère bleue") is None
        assert get_template_name("crée une sphère bleue") is None


# ---------------------------------------------------------------------------
# Fallback message brut — _PRODUCT_KEYWORDS
# ---------------------------------------------------------------------------

class TestProductRenderMessageFallback:

    @pytest.mark.parametrize("message", [
        "bouteille de parfum sur fond blanc",
        "mockup produit minimaliste",
        "packaging luxe",
        "packshot produit cosmétique",
        "rendu produit photoréaliste",
    ])
    def test_product_messages_trigger_product_render(self, message):
        assert get_template_name(message) == "product_render"
        assert select_template(message) is TEMPLATE_PRODUCT_RENDER

    @pytest.mark.parametrize("message", [
        "studio",
        "éclairage studio",
        "scène avec lumière studio douce",
    ])
    def test_studio_keyword_alone_does_not_trigger_product_render(self, message):
        assert get_template_name(message) != "product_render"
        assert select_template(message) is not TEMPLATE_PRODUCT_RENDER

    def test_interior_prompt_does_not_trigger_product_render(self):
        # "bureau" → interior_space, surtout pas product_render
        assert get_template_name("bureau lumineux") == "interior_space"

    def test_neutral_prompt_returns_none(self):
        assert get_template_name("crée une sphère bleue simple") is None


# ---------------------------------------------------------------------------
# build_blender_script — template_used propagé
# ---------------------------------------------------------------------------

class TestBuildBlenderScriptProductRender:

    def test_template_used_set_to_product_render_via_intent(self):
        """Prompt produit clair → intent.medium=product_render + sujet bouteille → template_used."""
        message = (
            "Crée un packshot produit d'une bouteille de parfum, "
            "fond neutre, éclairage studio softbox."
        )
        with (
            patch("app.clients.blender_client.generate_with_ollama",
                  return_value="```python\nimport bpy\n```"),
            patch("app.clients.blender_client.write_intent_json", return_value=None),
            patch("pathlib.Path.mkdir"),
            patch("pathlib.Path.write_text"),
        ):
            request = build_blender_script(message=message, context={}, request_id=_FAKE_ID)

        assert request.template_used == "product_render"
        assert request.creative_intent is not None

    def test_template_used_via_message_fallback_packaging(self):
        """Intent peut-être muet mais le message contient 'packaging' → fallback message → product_render."""
        message = "packaging"
        with (
            patch("app.clients.blender_client.generate_with_ollama",
                  return_value="```python\nimport bpy\n```"),
            patch("app.clients.blender_client.write_intent_json", return_value=None),
            patch("pathlib.Path.mkdir"),
            patch("pathlib.Path.write_text"),
        ):
            request = build_blender_script(message=message, context={}, request_id=_FAKE_ID)

        assert request.template_used == "product_render"


# ---------------------------------------------------------------------------
# manifest.future.template_used pour le cas produit
# ---------------------------------------------------------------------------

class TestManifestProductRender:

    def test_manifest_future_template_used_is_product_render(self):
        req = BlenderRequest(
            request_id=_FAKE_ID,
            script_content="import bpy",
            script_path=f"{_FAKE_DIR}/scene.py",
            output_path=f"{_FAKE_DIR}/scene.blend",
            render_path=f"{_FAKE_DIR}/preview.png",
            output_dir=_FAKE_DIR,
            timeout=60,
            source_prompt="packshot bouteille de parfum",
            creative_intent={"medium": "product_render", "subject_main": "bouteille"},
            template_used="product_render",
        )
        res = BlenderResult(
            status="success",
            request_id=_FAKE_ID,
            script_path=req.script_path,
            output_path=req.output_path,
            render_path=None,
            output_dir=_FAKE_DIR,
            returncode=0,
            stdout=None,
            stderr=None,
            error=None,
        )
        manifest = build_blender_manifest(req, res)
        assert manifest["future"]["template_used"] == "product_render"


# ---------------------------------------------------------------------------
# Intégrité du scaffold product_render
# ---------------------------------------------------------------------------

class TestProductRenderScaffoldIntegrity:

    def test_scaffold_contains_camera(self):
        assert "Camera" in TEMPLATE_PRODUCT_RENDER
        assert "bpy.context.scene.camera = cam" in TEMPLATE_PRODUCT_RENDER

    def test_scaffold_contains_key_light(self):
        assert "Key_Light" in TEMPLATE_PRODUCT_RENDER

    def test_scaffold_key_light_is_area(self):
        # Tolérant aux quotes : type='AREA'
        assert "type='AREA'" in TEMPLATE_PRODUCT_RENDER or 'type="AREA"' in TEMPLATE_PRODUCT_RENDER

    def test_scaffold_uses_output_blend_path(self):
        assert "OUTPUT_BLEND_PATH" in TEMPLATE_PRODUCT_RENDER
        assert "save_as_mainfile(filepath=OUTPUT_BLEND_PATH)" in TEMPLATE_PRODUCT_RENDER

    def test_scaffold_has_no_walls(self):
        """product_render ne doit pas contenir de Wall_* (réservé à interior_space)."""
        assert "Wall_" not in TEMPLATE_PRODUCT_RENDER

    def test_scaffold_has_backdrop_and_pedestal(self):
        assert "Backdrop_Plane" in TEMPLATE_PRODUCT_RENDER
        assert "Pedestal" in TEMPLATE_PRODUCT_RENDER
        assert "Product_Subject" in TEMPLATE_PRODUCT_RENDER


# ---------------------------------------------------------------------------
# H.4.6 — Stabilisation cadrage product_render
# ---------------------------------------------------------------------------

class TestProductRenderCadrageInvariants:
    """
    Verrouille les invariants H.4.6/H.4.6b du scaffold packshot :
    - produit visible (taille minimale), pas écrasé par le socle
    - backdrop vertical, fond et non objet dominant
    - caméra et lumières orientées via rotation_euler littérale (H.4.6b)
    """

    def test_scaffold_does_not_import_mathutils(self):
        """H.4.6b : pas d'import mathutils — toutes les rotations sont littérales."""
        assert "import mathutils" not in TEMPLATE_PRODUCT_RENDER

    def test_camera_uses_literal_euler(self):
        """H.4.6b : caméra orientée via rotation_euler = (a, b, c) littéraux, pas par calcul."""
        assert "cam.rotation_euler" in TEMPLATE_PRODUCT_RENDER
        assert "to_track_quat" not in TEMPLATE_PRODUCT_RENDER
        # Vérifier la présence d'une affectation littérale 3-floats sur la caméra
        import re
        m = re.search(
            r"cam\.rotation_euler\s*=\s*\(\s*[-\d.]+\s*,\s*[-\d.]+\s*,\s*[-\d.]+\s*\)",
            TEMPLATE_PRODUCT_RENDER,
        )
        assert m is not None, "cam.rotation_euler = (a, b, c) littéraux introuvable"

    def test_key_light_uses_literal_euler(self):
        """H.4.6b : Key_Light orienté via Euler littéraux."""
        import re
        m = re.search(
            r"key_light\.rotation_euler\s*=\s*\(\s*[-\d.]+\s*,\s*[-\d.]+\s*,\s*[-\d.]+\s*\)",
            TEMPLATE_PRODUCT_RENDER,
        )
        assert m is not None, "key_light.rotation_euler littéral introuvable"

    def test_fill_light_uses_literal_euler(self):
        """H.4.6b : Fill_Light orienté via Euler littéraux."""
        import re
        m = re.search(
            r"fill_light\.rotation_euler\s*=\s*\(\s*[-\d.]+\s*,\s*[-\d.]+\s*,\s*[-\d.]+\s*\)",
            TEMPLATE_PRODUCT_RENDER,
        )
        assert m is not None, "fill_light.rotation_euler littéral introuvable"

    def test_product_subject_has_visible_dimensions(self):
        """
        Le produit doit être assez large pour être visible en packshot.
        Verrou conservateur : radius >= 0.07 et depth >= 0.20.
        """
        import re
        # Cherche la ligne primitive_cylinder_add qui définit Product_Subject
        m = re.search(
            r"primitive_cylinder_add\(radius=([\d.]+),\s*depth=([\d.]+),"
            r"\s*location=\(0,\s*0,\s*([\d.]+)\)\)\s*\nproduct",
            TEMPLATE_PRODUCT_RENDER,
        )
        assert m is not None, "Product_Subject primitive_cylinder_add introuvable"
        radius = float(m.group(1))
        depth = float(m.group(2))
        z = float(m.group(3))
        assert radius >= 0.07, f"Product radius={radius} trop petit (<0.07)"
        assert depth >= 0.20, f"Product depth={depth} trop court (<0.20)"
        assert z >= 0.15, f"Product z={z} trop bas (sous le socle ?)"

    def test_pedestal_proportional_to_product(self):
        """
        Le socle ne doit pas écraser le produit.
        Verrou : pedestal radius <= 2.5 × product radius.
        """
        import re
        m_product = re.search(
            r"primitive_cylinder_add\(radius=([\d.]+),\s*depth=[\d.]+,"
            r"\s*location=\(0,\s*0,\s*[\d.]+\)\)\s*\nproduct",
            TEMPLATE_PRODUCT_RENDER,
        )
        m_pedestal = re.search(
            r"primitive_cylinder_add\(radius=([\d.]+),\s*depth=[\d.]+,"
            r"\s*location=\(0,\s*0,\s*[\d.]+\)\)\s*\npedestal",
            TEMPLATE_PRODUCT_RENDER,
        )
        assert m_product is not None and m_pedestal is not None
        product_r = float(m_product.group(1))
        pedestal_r = float(m_pedestal.group(1))
        ratio = pedestal_r / product_r
        assert ratio <= 2.5, (
            f"Pedestal radius={pedestal_r} trop large vs product radius={product_r} "
            f"(ratio={ratio:.2f}, max=2.5)"
        )

    def test_backdrop_is_vertical(self):
        """
        Le backdrop doit être un vrai mur vertical (rotation X ≈ π/2),
        pas un plan incliné qui plonge dans le frame.
        """
        import re
        m = re.search(
            r"backdrop\.rotation_euler\s*=\s*\(([\d.]+),\s*([\d.]+),\s*([\d.]+)\)",
            TEMPLATE_PRODUCT_RENDER,
        )
        assert m is not None, "Backdrop rotation_euler introuvable"
        x_rot = float(m.group(1))
        # π/2 ≈ 1.5708, tolérance ±5° (~0.087 rad)
        assert 1.48 <= x_rot <= 1.66, (
            f"Backdrop rotation X={x_rot} hors plage verticale (1.48-1.66 rad)"
        )

    def test_camera_close_enough_to_subject(self):
        """
        La caméra doit être à moins de 1 m du produit pour un packshot serré.
        """
        import re
        m = re.search(
            r"camera_add\(location=\(([-\d.]+),\s*([-\d.]+),\s*([-\d.]+)\)\)",
            TEMPLATE_PRODUCT_RENDER,
        )
        assert m is not None, "Camera location introuvable"
        cx, cy, cz = float(m.group(1)), float(m.group(2)), float(m.group(3))
        # Distance au centre produit (0, 0, ~0.18)
        dist = (cx ** 2 + cy ** 2 + (cz - 0.18) ** 2) ** 0.5
        assert dist <= 1.0, f"Caméra trop éloignée du sujet (d={dist:.2f} m, max=1.0)"
        # La caméra doit aussi être devant (Y négatif)
        assert cy < 0, f"Caméra doit avoir y<0 (devant le produit), y={cy}"

    def test_camera_lens_is_packshot_appropriate(self):
        """Lens entre 50 et 105 mm — focales typiques de packshot produit."""
        import re
        m = re.search(r"cam\.data\.lens\s*=\s*(\d+)", TEMPLATE_PRODUCT_RENDER)
        assert m is not None, "cam.data.lens introuvable"
        lens = int(m.group(1))
        assert 50 <= lens <= 105, f"Lens {lens}mm hors plage packshot (50-105)"

    def test_template_used_remains_product_render(self):
        """H.4.6/H.4.6b ne change pas le nom du template ni l'API publique."""
        intent = ArtisticIntent(medium="product_render", subject_main="bouteille")
        assert get_template_name_from_intent(intent) == "product_render"
        assert select_template_from_intent(intent) is TEMPLATE_PRODUCT_RENDER


# ---------------------------------------------------------------------------
# H.4.6b — Garde-fous anti-hallucination dans le scaffold
# ---------------------------------------------------------------------------

class TestProductRenderScaffoldGuards:
    """
    Verrouille les garde-fous H.4.6b qui réduisent les hallucinations LLM
    observées en runtime (imports OBJ, calcul vectoriel, renommage objets).
    """

    def test_scaffold_rules_block_present(self):
        """Un bloc AICORE_SCAFFOLD_RULES doit être présent en tête du scaffold."""
        assert "AICORE_SCAFFOLD_RULES" in TEMPLATE_PRODUCT_RENDER

    def test_rules_forbid_external_file_loading(self):
        """Le bloc de règles doit interdire le chargement de fichiers externes."""
        rules = TEMPLATE_PRODUCT_RENDER.split("AICORE_SCAFFOLD_RULES", 1)[1].split("Nettoyage")[0]
        rules_lower = rules.lower()
        assert "fichier externe" in rules_lower or "chemin externe" in rules_lower

    def test_rules_forbid_scene_import(self):
        """Le bloc de règles doit interdire l'import de scène externe."""
        rules = TEMPLATE_PRODUCT_RENDER.split("AICORE_SCAFFOLD_RULES", 1)[1].split("Nettoyage")[0]
        rules_lower = rules.lower()
        assert "scène" in rules_lower or "scene" in rules_lower
        assert "importer" in rules_lower or "import" in rules_lower

    def test_rules_lock_contract_names(self):
        """Les noms contractuels doivent être listés comme inchangeables."""
        rules = TEMPLATE_PRODUCT_RENDER.split("AICORE_SCAFFOLD_RULES", 1)[1].split("Nettoyage")[0]
        for name in ("Product_Subject", "Pedestal", "Backdrop_Plane",
                     "Camera", "Key_Light", "Fill_Light"):
            assert name in rules, f"Nom contractuel manquant dans les règles : {name}"

    def test_rules_state_camera_orientation_is_precomputed(self):
        """Le bloc doit dire que les rotations sont déjà fournies."""
        rules = TEMPLATE_PRODUCT_RENDER.split("AICORE_SCAFFOLD_RULES", 1)[1].split("Nettoyage")[0]
        rules_lower = rules.lower()
        assert "euler" in rules_lower
        assert ("recalculer" in rules_lower or "fournies" in rules_lower
                or "précalcul" in rules_lower or "precalcul" in rules_lower)

    def test_scaffold_does_not_use_to_track_quat(self):
        """H.4.6b : aucun appel à to_track_quat dans le scaffold."""
        assert "to_track_quat" not in TEMPLATE_PRODUCT_RENDER

    def test_scaffold_does_not_use_mathutils_vector(self):
        """H.4.6b : aucune utilisation de mathutils.Vector."""
        assert "mathutils.Vector" not in TEMPLATE_PRODUCT_RENDER
        assert "mathutils" not in TEMPLATE_PRODUCT_RENDER

    def test_scaffold_does_not_define_product_center_variable(self):
        """H.4.6b : variable globale _PRODUCT_CENTER supprimée."""
        assert "_PRODUCT_CENTER" not in TEMPLATE_PRODUCT_RENDER

    def test_scaffold_does_not_load_external_files(self):
        """H.4.6b : aucun pattern de chargement de fichier externe dans le scaffold."""
        import re
        # Patterns API d'import de fichier externe — interdits dans le code du scaffold
        assert "import_scene" not in TEMPLATE_PRODUCT_RENDER
        assert "meshes.load" not in TEMPLATE_PRODUCT_RENDER
        assert "bpy.ops.import_" not in TEMPLATE_PRODUCT_RENDER
        # Extensions de fichier 3D — doivent apparaître nulle part comme littéral
        # (tolérer .objects de Blender mais bloquer .obj/.fbx/.gltf comme extensions).
        for ext in (".obj", ".fbx", ".gltf", ".glb", ".dae", ".stl", ".ply"):
            # Cherche l'extension entourée d'apostrophes/guillemets/espaces/finlignes
            # — typique d'un chemin de fichier — pas en tant que ".objects" etc.
            pattern = re.escape(ext) + r"(?![a-zA-Z_])"
            assert not re.search(pattern, TEMPLATE_PRODUCT_RENDER), (
                f"Extension de fichier 3D détectée dans le scaffold : {ext}"
            )

    def test_scaffold_does_not_offer_primitive_choice_for_subject(self):
        """H.4.6b : le scaffold ne propose plus au LLM de remplacer Product_Subject."""
        # H.4.6 disait "Le LLM peut adapter la primitive (cube, sphère, capsule)" — RETIRÉ.
        assert "Le LLM peut adapter la primitive" not in TEMPLATE_PRODUCT_RENDER
        # Le sujet doit rester un proxy stable.
        assert 'product.name = "Product_Subject"' in TEMPLATE_PRODUCT_RENDER
