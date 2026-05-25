"""
Tests unitaires — blender_ast_guard.analyze_scene_py (H.4.7).

Fonctions pures, aucun subprocess Blender requis.
"""
from __future__ import annotations

from app.engine.blender_ast_guard import (
    V_AST_UNPARSEABLE,
    V_EXTERNAL_ASSET_PREFIX,
    V_FALLBACK_CUBE_SUN_ONLY,
    V_MESHES_NEW_WITHOUT_GEOMETRY,
    V_NO_CAMERA_ASSIGNMENT,
    V_NO_DELETE_DEFAULT,
    V_NO_PRIMITIVE_ADD,
    V_OPEN_NON_PIPELINE_FILE,
    V_PLACEHOLDER_PATH,
    V_SCRIPT_TOO_SHORT,
    V_TEMPLATE_FORBIDDEN_PREFIX,
    V_TEMPLATE_REQUIRED_PREFIX,
    analyze_scene_py,
)
from app.engine.blender_templates import (
    TEMPLATE_INTERIOR_SPACE,
    TEMPLATE_PRODUCT_RENDER,
)


# ---------------------------------------------------------------------------
# Scaffold canoniques — must pass
# ---------------------------------------------------------------------------

def test_passed_on_canonical_product_render_scaffold():
    # Le scaffold canonique référence OUTPUT_BLEND_PATH (variable injectée)
    # et doit être audit-clean côté AST guard.
    report = analyze_scene_py(TEMPLATE_PRODUCT_RENDER, "product_render")
    assert report["status"] == "passed", report["violations"]
    assert report["violations"] == []
    assert report["checks"]["ast_parseable"]["status"] == "passed"


def test_passed_on_canonical_interior_space_scaffold():
    report = analyze_scene_py(TEMPLATE_INTERIOR_SPACE, "interior_space")
    assert report["status"] == "passed", report["violations"]
    assert report["violations"] == []


# ---------------------------------------------------------------------------
# AST non parseable
# ---------------------------------------------------------------------------

def test_ast_unparseable_returns_unparseable_violation():
    broken = "import bpy\nbpy.ops.mesh.primitive_cube_add(  # missing close"
    report = analyze_scene_py(broken, None)
    assert V_AST_UNPARSEABLE in report["violations"]
    assert report["status"] == "degraded"
    assert report["checks"]["ast_parseable"]["status"] == "degraded"
    assert report["metrics"]["ast_parse_error"] is not None


# ---------------------------------------------------------------------------
# External assets — obj / fbx / gltf
# ---------------------------------------------------------------------------

def _wrap_minimal(body: str) -> str:
    """Préfixe un corps de script par les éléments structurels suffisants
    pour que SEUL le check ciblé déclenche une violation. Utile pour isoler."""
    return (
        "import bpy\n"
        "bpy.ops.object.select_all(action='SELECT')\n"
        "bpy.ops.object.delete()\n"
        "bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))\n"
        "bpy.ops.object.camera_add(location=(7, -7, 5))\n"
        "bpy.context.scene.camera = bpy.context.object\n"
        "bpy.ops.object.light_add(type='SUN', location=(4, 4, 6))\n"
        "bpy.ops.object.light_add(type='AREA', location=(2, 2, 2))\n"
        + body
    )


def test_external_asset_obj_detected():
    code = _wrap_minimal("bpy.ops.import_scene.obj(filepath='asset.obj')\n")
    report = analyze_scene_py(code, None)
    assert f"{V_EXTERNAL_ASSET_PREFIX}obj" in report["violations"]
    assert report["metrics"]["external_load_count"] == 1


def test_external_asset_fbx_detected():
    code = _wrap_minimal("bpy.ops.import_scene.fbx(filepath='asset.fbx')\n")
    report = analyze_scene_py(code, None)
    assert f"{V_EXTERNAL_ASSET_PREFIX}fbx" in report["violations"]


def test_external_asset_wm_gltf_import_detected():
    code = _wrap_minimal("bpy.ops.wm.gltf_import(filepath='asset.gltf')\n")
    report = analyze_scene_py(code, None)
    assert f"{V_EXTERNAL_ASSET_PREFIX}gltf" in report["violations"]


# ---------------------------------------------------------------------------
# Placeholder paths
# ---------------------------------------------------------------------------

def test_placeholder_path_detected_in_images_load():
    code = _wrap_minimal(
        "img = bpy.data.images.load('/path/to/texture.png')\n"
    )
    report = analyze_scene_py(code, None)
    assert V_PLACEHOLDER_PATH in report["violations"]
    # Le check 'open_non_pipeline_file' s'allume aussi (chemin littéral non pipeline).
    assert V_OPEN_NON_PIPELINE_FILE in report["violations"]


def test_placeholder_path_your_model():
    code = _wrap_minimal(
        "name = 'your_model.obj'\n"
    )
    report = analyze_scene_py(code, None)
    assert V_PLACEHOLDER_PATH in report["violations"]


def test_open_with_pipeline_var_is_ok():
    # open(OUTPUT_BLEND_PATH) ne doit PAS lever de violation
    code = _wrap_minimal(
        "f = open(OUTPUT_BLEND_PATH)\n"
    )
    report = analyze_scene_py(code, None)
    assert V_OPEN_NON_PIPELINE_FILE not in report["violations"]


# ---------------------------------------------------------------------------
# Primitive geometry / meshes.new
# ---------------------------------------------------------------------------

def test_no_primitive_add_when_only_mesh_new_without_pydata():
    code = (
        "import bpy\n"
        "bpy.ops.object.select_all(action='SELECT')\n"
        "bpy.ops.object.delete()\n"
        "mesh = bpy.data.meshes.new('M')\n"
        "obj = bpy.data.objects.new('O', mesh)\n"
        "bpy.context.collection.objects.link(obj)\n"
        "bpy.ops.object.camera_add(location=(0, 0, 0))\n"
        "bpy.ops.object.light_add(type='SUN')\n"
    )
    report = analyze_scene_py(code, None)
    assert V_NO_PRIMITIVE_ADD in report["violations"]
    assert V_MESHES_NEW_WITHOUT_GEOMETRY in report["violations"]


def test_meshes_new_with_from_pydata_is_ok():
    code = (
        "import bpy\n"
        "bpy.ops.object.select_all(action='SELECT')\n"
        "bpy.ops.object.delete()\n"
        "verts = [(0,0,0),(1,0,0),(0,1,0)]\n"
        "faces = [(0,1,2)]\n"
        "mesh = bpy.data.meshes.new('M')\n"
        "mesh.from_pydata(verts, [], faces)\n"
        "obj = bpy.data.objects.new('O', mesh)\n"
        "bpy.ops.object.camera_add(location=(0,0,0))\n"
        "bpy.context.scene.camera = bpy.context.object\n"
        "bpy.ops.object.light_add(type='SUN')\n"
    )
    report = analyze_scene_py(code, None)
    assert V_NO_PRIMITIVE_ADD not in report["violations"]
    assert V_MESHES_NEW_WITHOUT_GEOMETRY not in report["violations"]


def test_from_pydata_with_empty_lists_does_not_count_as_geometry():
    code = (
        "import bpy\n"
        "bpy.ops.object.select_all(action='SELECT')\n"
        "bpy.ops.object.delete()\n"
        "mesh = bpy.data.meshes.new('M')\n"
        "mesh.from_pydata([], [], [])\n"
        "bpy.ops.object.camera_add(location=(0,0,0))\n"
        "bpy.ops.object.light_add(type='SUN')\n"
    )
    report = analyze_scene_py(code, None)
    assert V_MESHES_NEW_WITHOUT_GEOMETRY in report["violations"]
    assert V_NO_PRIMITIVE_ADD in report["violations"]


# ---------------------------------------------------------------------------
# Camera assignment
# ---------------------------------------------------------------------------

def test_no_camera_assignment_detected():
    code = (
        "import bpy\n"
        "bpy.ops.object.select_all(action='SELECT')\n"
        "bpy.ops.object.delete()\n"
        "bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))\n"
        "bpy.ops.object.light_add(type='SUN', location=(4, 4, 6))\n"
    )
    report = analyze_scene_py(code, None)
    assert V_NO_CAMERA_ASSIGNMENT in report["violations"]


def test_camera_add_only_is_ok():
    # camera_add suffit, l'assignation explicite n'est pas requise
    code = (
        "import bpy\n"
        "bpy.ops.object.select_all(action='SELECT')\n"
        "bpy.ops.object.delete()\n"
        "bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))\n"
        "bpy.ops.object.camera_add(location=(0, 0, 0))\n"
        "bpy.ops.object.light_add(type='SUN')\n"
        "bpy.ops.object.light_add(type='AREA')\n"
    )
    report = analyze_scene_py(code, None)
    assert V_NO_CAMERA_ASSIGNMENT not in report["violations"]


# ---------------------------------------------------------------------------
# Script size
# ---------------------------------------------------------------------------

def test_script_too_short_detected():
    report = analyze_scene_py("import bpy\nbpy.ops.mesh.primitive_cube_add()\n", None)
    assert V_SCRIPT_TOO_SHORT in report["violations"]


def test_empty_string_returns_skipped():
    report = analyze_scene_py("", None)
    assert report["status"] == "skipped"
    assert report["violations"] == []


# ---------------------------------------------------------------------------
# Template required / forbidden
# ---------------------------------------------------------------------------

def test_template_required_missing_for_product_render():
    # Scaffold quasi-correct mais sans Product_Subject
    code = (
        "import bpy\n"
        "bpy.ops.object.select_all(action='SELECT')\n"
        "bpy.ops.object.delete()\n"
        "bpy.ops.mesh.primitive_plane_add(size=4)\n"
        "obj = bpy.context.object\n"
        "obj.name = 'Backdrop_Plane'\n"
        "bpy.ops.mesh.primitive_cylinder_add()\n"
        "obj = bpy.context.object\n"
        "obj.name = 'Pedestal'\n"
        "bpy.ops.object.camera_add()\n"
        "cam = bpy.context.object\n"
        "cam.name = 'Camera'\n"
        "bpy.context.scene.camera = cam\n"
        "bpy.ops.object.light_add(type='AREA')\n"
        "light = bpy.context.object\n"
        "light.name = 'Key_Light'\n"
    )
    report = analyze_scene_py(code, "product_render")
    assert f"{V_TEMPLATE_REQUIRED_PREFIX}Product_Subject" in report["violations"]


def test_template_forbidden_prefix_for_product_render():
    code = (
        "import bpy\n"
        "bpy.ops.object.select_all(action='SELECT')\n"
        "bpy.ops.object.delete()\n"
        "bpy.ops.mesh.primitive_plane_add()\n"
        "backdrop = bpy.context.object\n"
        "backdrop.name = 'Backdrop_Plane'\n"
        "bpy.ops.mesh.primitive_cube_add()\n"
        "wall = bpy.context.object\n"
        "wall.name = 'Wall_Back'\n"  # interdit en product_render
        "bpy.ops.mesh.primitive_cylinder_add()\n"
        "pedestal = bpy.context.object\n"
        "pedestal.name = 'Pedestal'\n"
        "bpy.ops.mesh.primitive_cylinder_add()\n"
        "prod = bpy.context.object\n"
        "prod.name = 'Product_Subject'\n"
        "bpy.ops.object.camera_add()\n"
        "cam = bpy.context.object\n"
        "cam.name = 'Camera'\n"
        "bpy.context.scene.camera = cam\n"
        "bpy.ops.object.light_add(type='AREA')\n"
        "kl = bpy.context.object\n"
        "kl.name = 'Key_Light'\n"
    )
    report = analyze_scene_py(code, "product_render")
    assert f"{V_TEMPLATE_FORBIDDEN_PREFIX}Wall_" in report["violations"]


def test_unknown_template_skips_template_checks():
    code = _wrap_minimal("")
    report = analyze_scene_py(code, "not_a_real_template")
    # Aucune violation namespacée template_*
    for v in report["violations"]:
        assert not v.startswith(V_TEMPLATE_REQUIRED_PREFIX)
        assert not v.startswith(V_TEMPLATE_FORBIDDEN_PREFIX)


def test_no_template_skips_template_checks():
    code = _wrap_minimal("")
    report = analyze_scene_py(code, None)
    for v in report["violations"]:
        assert not v.startswith(V_TEMPLATE_REQUIRED_PREFIX)
        assert not v.startswith(V_TEMPLATE_FORBIDDEN_PREFIX)


# ---------------------------------------------------------------------------
# Fallback minimal + delete default
# ---------------------------------------------------------------------------

def test_fallback_cube_sun_only_detected():
    code = (
        "import bpy\n"
        "bpy.ops.object.select_all(action='SELECT')\n"
        "bpy.ops.object.delete()\n"
        "bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))\n"
        "bpy.ops.object.camera_add(location=(7, -7, 5))\n"
        "bpy.context.scene.camera = bpy.context.object\n"
        "bpy.ops.object.light_add(type='SUN', location=(4, 4, 6))\n"
    )
    report = analyze_scene_py(code, None)
    assert V_FALLBACK_CUBE_SUN_ONLY in report["violations"]


def test_no_delete_default_detected():
    code = (
        "import bpy\n"
        "bpy.ops.mesh.primitive_cube_add(location=(0, 0, 0))\n"
        "bpy.ops.mesh.primitive_uv_sphere_add(location=(2, 0, 0))\n"
        "bpy.ops.object.camera_add(location=(7, -7, 5))\n"
        "bpy.context.scene.camera = bpy.context.object\n"
        "bpy.ops.object.light_add(type='SUN', location=(4, 4, 6))\n"
        "bpy.ops.object.light_add(type='AREA')\n"
    )
    report = analyze_scene_py(code, None)
    assert V_NO_DELETE_DEFAULT in report["violations"]


# ---------------------------------------------------------------------------
# Status & schéma de rapport
# ---------------------------------------------------------------------------

def test_status_passed_when_no_violations():
    report = analyze_scene_py(TEMPLATE_PRODUCT_RENDER, "product_render")
    assert report["status"] == "passed"


def test_status_degraded_when_violations():
    code = "import bpy\n"  # trop court, sans rien
    report = analyze_scene_py(code, None)
    assert report["status"] == "degraded"


def test_report_has_metrics_keys():
    report = analyze_scene_py(TEMPLATE_PRODUCT_RENDER, "product_render")
    metrics = report["metrics"]
    assert "raw_code_length" in metrics
    assert "primitive_add_count" in metrics
    assert "external_load_count" in metrics
    assert "ast_parse_error" in metrics
    assert metrics["raw_code_length"] == len(TEMPLATE_PRODUCT_RENDER)
    assert metrics["primitive_add_count"] >= 3  # plane + cylinder + cylinder


def test_report_has_all_checks_keys():
    report = analyze_scene_py(TEMPLATE_PRODUCT_RENDER, "product_render")
    expected = {
        "ast_parseable",
        "no_external_assets",
        "no_placeholder_paths",
        "has_primitive_geometry",
        "meshes_new_has_from_pydata",
        "template_required_objects",
        "script_min_size",
        "active_camera_assigned",
        "fallback_cube_sun_only",
        "delete_default_present",
    }
    assert set(report["checks"].keys()) == expected
