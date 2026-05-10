from app.engine.blender_script_quality import analyze_blender_script_quality, detect_blender_script


_GOOD_CUBE = """
import bpy

bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete()
bpy.ops.mesh.primitive_cube_add(size=2, location=(0, 0, 0))
"""

_MANUAL_MESH = """
import bpy

verts = [(1, 1, -1), (1, -1, -1), (-1, -1, -1), (-1, 1, -1),
         (1, 1, 1), (1, -1, 1), (-1, -1, 1), (-1, 1, 1)]
faces = [(0, 1, 2, 3), (4, 7, 6, 5), (0, 4, 5, 1),
         (1, 5, 6, 2), (2, 6, 7, 3), (4, 0, 3, 7)]

mesh = bpy.data.meshes.new("MyCube")
obj = bpy.data.objects.new("MyCube", mesh)
bpy.context.collection.objects.link(obj)
mesh.from_pydata(verts, [], faces)
mesh.update()
"""

_EMPTY_NAMED_MESH = """
import bpy

mesh = bpy.data.meshes.new("Cube")
obj = bpy.data.objects.new("Cube", mesh)
bpy.context.collection.objects.link(obj)
"""

_RENDER_WITHOUT_REQUEST = """
import bpy

bpy.ops.mesh.primitive_cube_add(size=2, location=(0, 0, 0))
bpy.ops.render.render(write_still=True)
"""

_SUBPROCESS_SCRIPT = """
import bpy
import subprocess

subprocess.run(["blender", "--background"])
bpy.ops.mesh.primitive_cube_add(size=2, location=(0, 0, 0))
"""


def test_good_cube_script_has_no_violations():
    result = analyze_blender_script_quality("créer un cube simple", _GOOD_CUBE)
    assert result["is_blender"] is True
    assert result["violations"] == []


def test_empty_named_mesh_without_from_pydata_flags_violations():
    result = analyze_blender_script_quality("crée un mesh", _EMPTY_NAMED_MESH)
    assert result["is_blender"] is True
    assert "meshes_new_without_from_pydata" in result["violations"]
    assert "empty_named_mesh_without_geometry" in result["violations"]


def test_manual_mesh_with_from_pydata_has_no_violations():
    result = analyze_blender_script_quality("crée un cube avec un mesh manuel", _MANUAL_MESH)
    assert result["is_blender"] is True
    assert result["violations"] == []


def test_render_without_explicit_request_flags_violation():
    result = analyze_blender_script_quality("créer un cube", _RENDER_WITHOUT_REQUEST)
    assert result["is_blender"] is True
    assert "render_called_without_request" in result["violations"]


def test_subprocess_flags_violation():
    result = analyze_blender_script_quality("script blender", _SUBPROCESS_SCRIPT)
    assert result["is_blender"] is True
    assert "subprocess_or_os_system_forbidden" in result["violations"]


def test_detect_blender_script_returns_true_for_bpy_content():
    assert detect_blender_script("import bpy\nbpy.ops.mesh.primitive_cube_add()") is True
    assert detect_blender_script("bpy.ops.object.delete()") is True
    assert detect_blender_script("plain text without blender content") is False


def test_quality_module_does_not_import_bpy():
    import inspect
    from app.engine import blender_script_quality

    source = inspect.getsource(blender_script_quality)
    real_bpy_imports = [
        line.strip()
        for line in source.splitlines()
        if line.strip().startswith("import bpy") or line.strip().startswith("from bpy")
    ]
    assert real_bpy_imports == []


# ---------------------------------------------------------------------------
# Nouvelles violations : nodes_clear, metallic, camera_missing
# ---------------------------------------------------------------------------

_NODES_CLEAR_THEN_ACCESS_BRACKET = """
import bpy

mat = bpy.data.materials.new(name="Metal")
mat.use_nodes = True
nodes = mat.node_tree.nodes
nodes.clear()
output_node = nodes["Material Output"]
bsdf = nodes.new(type='ShaderNodeBsdfPrincipled')
"""

_NODES_CLEAR_THEN_ACCESS_GET = """
import bpy

mat = bpy.data.materials.new(name="Metal")
mat.use_nodes = True
nodes = mat.node_tree.nodes
nodes.clear()
output_node = nodes.get("Material Output")
bsdf = nodes.new(type='ShaderNodeBsdfPrincipled')
"""

_GOOD_METALLIC_SCRIPT = """
import bpy

bpy.ops.mesh.primitive_cube_add(size=2, location=(0, 0, 0))
mat = bpy.data.materials.new(name="Metal")
mat.use_nodes = True
bsdf = mat.node_tree.nodes.get("Principled BSDF")
bsdf.inputs["Metallic"].default_value = 1.0
bsdf.inputs["Roughness"].default_value = 0.2
bpy.context.object.data.materials.append(mat)
"""

_METALLIC_WITHOUT_METALLIC_VALUE = """
import bpy

bpy.ops.mesh.primitive_cube_add(size=2, location=(0, 0, 0))
mat = bpy.data.materials.new(name="Metal")
mat.use_nodes = True
bpy.context.object.data.materials.append(mat)
"""

_SCENE_WITHOUT_CAMERA = """
import bpy

bpy.ops.mesh.primitive_cube_add(size=2, location=(0, 0, 0))
bpy.ops.object.light_add(type='SUN', location=(4, 4, 6))
"""

_SCENE_WITH_CAMERA = """
import bpy

bpy.ops.mesh.primitive_cube_add(size=2, location=(0, 0, 0))
bpy.ops.object.camera_add(location=(7, -7, 5))
bpy.ops.object.light_add(type='SUN', location=(4, 4, 6))
"""


def test_nodes_clear_then_bracket_access_flags_violation():
    """nodes.clear() suivi de nodes[...] doit être détecté."""
    result = analyze_blender_script_quality("créer un matériau métallique", _NODES_CLEAR_THEN_ACCESS_BRACKET)
    assert result["is_blender"] is True
    assert "nodes_clear_then_node_access" in result["violations"]


def test_nodes_clear_then_get_access_flags_violation():
    """nodes.clear() suivi de nodes.get(...) doit aussi être détecté."""
    result = analyze_blender_script_quality("créer un matériau métallique", _NODES_CLEAR_THEN_ACCESS_GET)
    assert result["is_blender"] is True
    assert "nodes_clear_then_node_access" in result["violations"]


def test_metallic_requested_without_metallic_value_flags_violation():
    """Message demande un métal mais le script ne configure pas Metallic."""
    result = analyze_blender_script_quality("crée un cube métallique", _METALLIC_WITHOUT_METALLIC_VALUE)
    assert result["is_blender"] is True
    assert "metallic_requested_without_metallic_value" in result["violations"]


def test_good_metallic_script_has_no_metallic_violation():
    """Script avec Metallic configuré → pas de violation métallique."""
    result = analyze_blender_script_quality("crée un cube métallique", _GOOD_METALLIC_SCRIPT)
    assert result["is_blender"] is True
    assert "metallic_requested_without_metallic_value" not in result["violations"]


def test_scene_without_camera_flags_violation():
    """Scène demandée sans camera_add dans le script → violation informative."""
    result = analyze_blender_script_quality("crée une scène Blender avec un cube", _SCENE_WITHOUT_CAMERA)
    assert result["is_blender"] is True
    assert "camera_missing_in_script" in result["violations"]


def test_scene_with_camera_no_camera_violation():
    """Scène avec camera_add → pas de violation caméra."""
    result = analyze_blender_script_quality("crée une scène Blender avec un cube", _SCENE_WITH_CAMERA)
    assert result["is_blender"] is True
    assert "camera_missing_in_script" not in result["violations"]


def test_non_scene_request_no_camera_violation():
    """Requête non-scène (ex. objet seul) → pas de violation caméra même sans caméra."""
    result = analyze_blender_script_quality("crée un cube simple", _SCENE_WITHOUT_CAMERA)
    assert result["is_blender"] is True
    assert "camera_missing_in_script" not in result["violations"]
