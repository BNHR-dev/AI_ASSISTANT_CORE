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
