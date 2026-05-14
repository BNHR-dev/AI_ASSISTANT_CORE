"""
Tests — Blender Blocking Contract Lite + Template interior_space (Phase 1).

Vérifie :
- blender_blocking_contract : détection de violations sur scripts valides et invalides
- blender_templates : contenu du template interior_space
- blender_templates : sélection de template par mots-clés
- intégration : blender_client importe les nouveaux modules sans erreur
- aucun import bpy réel dans les nouveaux modules
"""

from __future__ import annotations

import pytest

from app.engine.blender_blocking_contract import check_blender_blocking_contract
from app.engine.blender_templates import (
    TEMPLATE_INTERIOR_SPACE,
    get_template_name,
    select_template,
)


# ---------------------------------------------------------------------------
# Fixtures — scripts de test
# ---------------------------------------------------------------------------

_VALID_BLOCKING_SCRIPT = """
import bpy

bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete()

bpy.ops.mesh.primitive_plane_add(size=10, location=(0, 0, 0))
floor = bpy.context.object
floor.name = "Floor_Plane"

bpy.ops.mesh.primitive_cylinder_add(radius=0.3, depth=1.8, location=(0, 0, 0.9))
subject = bpy.context.object
subject.name = "Main_Subject"

bpy.ops.object.camera_add(location=(0, -6, 2.2))
cam = bpy.context.object
bpy.context.scene.camera = cam

bpy.ops.object.light_add(type='SUN', location=(4, -4, 6))
light = bpy.context.object
light.name = "Key_Light"

bpy.ops.wm.save_as_mainfile(filepath=OUTPUT_BLEND_PATH)
"""

_EMPTY_SCRIPT = """
import bpy
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete()
"""

_SCRIPT_NO_FLOOR = """
import bpy

bpy.ops.mesh.primitive_cylinder_add(radius=0.3, depth=1.8, location=(0, 0, 0.9))
subject = bpy.context.object
subject.name = "Main_Subject"

bpy.ops.object.camera_add(location=(0, -6, 2.2))
cam = bpy.context.object
bpy.context.scene.camera = cam

bpy.ops.object.light_add(type='SUN', location=(4, -4, 6))
"""

_SCRIPT_NO_CAMERA = """
import bpy

bpy.ops.mesh.primitive_plane_add(size=10, location=(0, 0, 0))
floor = bpy.context.object
floor.name = "Floor_Plane"

bpy.ops.mesh.primitive_cylinder_add(radius=0.3, depth=1.8, location=(0, 0, 0.9))
subject = bpy.context.object
subject.name = "Main_Subject"

bpy.ops.object.light_add(type='SUN', location=(4, -4, 6))
"""

_SCRIPT_NO_LIGHT = """
import bpy

bpy.ops.mesh.primitive_plane_add(size=10, location=(0, 0, 0))
floor = bpy.context.object
floor.name = "Floor_Plane"

bpy.ops.mesh.primitive_cylinder_add(radius=0.3, depth=1.8, location=(0, 0, 0.9))
subject = bpy.context.object
subject.name = "Main_Subject"

bpy.ops.object.camera_add(location=(0, -6, 2.2))
cam = bpy.context.object
bpy.context.scene.camera = cam
"""

_SCRIPT_HARDCODED_PATH = """
import bpy

bpy.ops.mesh.primitive_plane_add(size=10, location=(0, 0, 0))
floor = bpy.context.object
floor.name = "Floor_Plane"

bpy.ops.mesh.primitive_cylinder_add(radius=0.3, depth=1.8, location=(0, 0, 0.9))
subject = bpy.context.object
subject.name = "Main_Subject"

bpy.ops.object.camera_add(location=(0, -6, 2.2))
cam = bpy.context.object
bpy.context.scene.camera = cam

bpy.ops.object.light_add(type='SUN', location=(4, -4, 6))

bpy.ops.wm.save_as_mainfile(filepath="/home/user/output.blend")
"""


# ---------------------------------------------------------------------------
# Tests — blender_blocking_contract
# ---------------------------------------------------------------------------

class TestBlenderBlockingContract:

    def test_valid_script_passes_contract(self):
        result = check_blender_blocking_contract(_VALID_BLOCKING_SCRIPT)
        assert result["static_contract_passed"] is True
        assert result["static_contract_violations"] == []

    def test_empty_script_flags_scene_likely_empty(self):
        result = check_blender_blocking_contract(_EMPTY_SCRIPT)
        assert "scene_likely_empty" in result["static_contract_violations"]
        assert result["static_contract_passed"] is False

    def test_script_without_floor_flags_violation(self):
        result = check_blender_blocking_contract(_SCRIPT_NO_FLOOR)
        assert "no_floor_or_space" in result["static_contract_violations"]

    def test_script_without_camera_flags_violation(self):
        result = check_blender_blocking_contract(_SCRIPT_NO_CAMERA)
        assert "no_camera_in_script" in result["static_contract_violations"]

    def test_script_without_light_flags_violation(self):
        result = check_blender_blocking_contract(_SCRIPT_NO_LIGHT)
        assert "no_light_in_script" in result["static_contract_violations"]

    def test_hardcoded_path_flags_violation(self):
        result = check_blender_blocking_contract(_SCRIPT_HARDCODED_PATH)
        assert "hardcoded_output_path" in result["static_contract_violations"]

    def test_contract_module_does_not_import_bpy(self):
        """Le module ne doit pas importer bpy comme module Python réel."""
        from app.engine import blender_blocking_contract
        # Vérifie que bpy n'est pas dans les attributs importés du module
        assert not hasattr(blender_blocking_contract, "bpy"), (
            "blender_blocking_contract ne doit pas importer bpy"
        )


# ---------------------------------------------------------------------------
# Tests — template interior_space contenu
# ---------------------------------------------------------------------------

class TestTemplateInteriorSpace:

    def test_template_contains_import_bpy(self):
        assert "import bpy" in TEMPLATE_INTERIOR_SPACE

    def test_template_contains_camera_add(self):
        assert "camera_add" in TEMPLATE_INTERIOR_SPACE

    def test_template_assigns_active_camera(self):
        assert "bpy.context.scene.camera" in TEMPLATE_INTERIOR_SPACE

    def test_template_contains_light_add(self):
        assert "light_add" in TEMPLATE_INTERIOR_SPACE

    def test_template_contains_key_light_name(self):
        assert "Key_Light" in TEMPLATE_INTERIOR_SPACE

    def test_template_contains_floor(self):
        assert "Floor_Plane" in TEMPLATE_INTERIOR_SPACE

    def test_template_contains_main_subject(self):
        assert "Main_Subject" in TEMPLATE_INTERIOR_SPACE

    def test_template_contains_collections(self):
        assert "SCENE" in TEMPLATE_INTERIOR_SPACE
        assert "PROPS" in TEMPLATE_INTERIOR_SPACE

    def test_template_uses_output_blend_path_placeholder(self):
        # Le pipeline injecte OUTPUT_BLEND_PATH — pas de chemin hardcodé
        assert "OUTPUT_BLEND_PATH" in TEMPLATE_INTERIOR_SPACE
        assert '"/home/' not in TEMPLATE_INTERIOR_SPACE
        assert '"C:\\' not in TEMPLATE_INTERIOR_SPACE

    def test_template_passes_blocking_contract(self):
        result = check_blender_blocking_contract(TEMPLATE_INTERIOR_SPACE)
        assert result["static_contract_passed"] is True, (
            f"Template interior_space viole le contrat : {result['static_contract_violations']}"
        )

    def test_template_module_does_not_import_bpy(self):
        """Le module ne doit pas importer bpy comme module Python réel."""
        from app.engine import blender_templates
        # Vérifie que bpy n'est pas dans les attributs importés du module
        assert not hasattr(blender_templates, "bpy"), (
            "blender_templates ne doit pas importer bpy"
        )


# ---------------------------------------------------------------------------
# Tests — sélection de template
# ---------------------------------------------------------------------------

class TestSelectTemplate:

    # Cas qui doivent sélectionner interior_space
    @pytest.mark.parametrize("message", [
        "crée une scène de bureau intérieure",
        "génère un intérieur avec une table",
        "une scène dans une room",
        "modélise une pièce simple",
        "je veux un couloir",
        "crée une scène indoor",
        "bureau avec une fenêtre",
        "scène intérieure avec un personnage",
        "un salon minimaliste",
    ])
    def test_interior_keywords_select_interior_space(self, message):
        assert get_template_name(message) == "interior_space"
        assert select_template(message) is not None
        assert "import bpy" in select_template(message)

    # Cas qui ne doivent PAS sélectionner de template (fallback)
    @pytest.mark.parametrize("message", [
        "génère un cube métallique",
        "crée une sphère bleue",
        "script blender simple avec une lumière",
        "modélise un arbre",
        "une scène extérieure avec des montagnes",
        "fais un rendu de voiture",
    ])
    def test_non_interior_keywords_return_none(self, message):
        assert get_template_name(message) is None
        assert select_template(message) is None


# ---------------------------------------------------------------------------
# Test d'import — blender_client n'est pas cassé par les nouveaux imports
# ---------------------------------------------------------------------------

class TestBlenderClientImports:

    def test_blender_client_imports_without_error(self):
        """Vérifie que blender_client importe correctement les nouveaux modules."""
        from app.clients import blender_client
        assert hasattr(blender_client, "select_template")
        assert hasattr(blender_client, "get_template_name")
        assert hasattr(blender_client, "build_blender_script")
