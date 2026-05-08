from app.engine.blender_script_quality import analyze_blender_script_quality


_GOOD_SCRIPT = """
import bpy
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete()
bpy.ops.mesh.primitive_cube_add(size=2, location=(0, 0, 0))
"""

_BAD_SCRIPT = """
import bpy
mesh = bpy.data.meshes.new("Cube")
obj = bpy.data.objects.new("Cube", mesh)
bpy.context.collection.objects.link(obj)
"""


def test_quality_report_passed_for_good_blender_script():
    report = analyze_blender_script_quality("créer un cube", _GOOD_SCRIPT)
    assert report["is_blender"] is True
    assert report["violations"] == []
    assert len(report["violations"]) == 0


def test_quality_report_violation_for_empty_named_mesh():
    report = analyze_blender_script_quality("créer un mesh", _BAD_SCRIPT)
    assert report["is_blender"] is True
    assert "meshes_new_without_from_pydata" in report["violations"]
    assert len(report["violations"]) > 0


def test_quality_report_does_not_block_execute_assembly(monkeypatch):
    from app.engine.executor import execute_request

    monkeypatch.setattr(
        "app.engine.step_executor.generate_with_ollama",
        lambda model, prompt: _GOOD_SCRIPT,
    )

    result = execute_request("créer un cube blender")

    assert result["output"]
    assert "blender_quality_report" in result
    assert result["blender_quality_report"] is not None
    assert result["blender_quality_report"]["is_blender"] is True
    assert result["blender_quality_report"]["passed"] is True
    assert result["blender_quality_report"]["violations"] == []


def test_execute_request_bad_blender_script_has_violations(monkeypatch):
    from app.engine.executor import execute_request

    monkeypatch.setattr(
        "app.engine.step_executor.generate_with_ollama",
        lambda model, prompt: _BAD_SCRIPT,
    )

    result = execute_request("créer un cube blender")

    assert "blender_quality_report" in result
    report = result["blender_quality_report"]
    assert report is not None
    assert report["is_blender"] is True
    assert report["passed"] is False
    assert len(report["violations"]) > 0


def test_non_blender_output_produces_no_blender_report():
    report = analyze_blender_script_quality(
        "explique les embeddings",
        "Les embeddings sont des représentations vectorielles de données.",
    )
    assert report["is_blender"] is False
    assert report["violations"] == []


def test_quality_module_has_no_bpy_import_or_subprocess():
    import inspect
    from app.engine import blender_script_quality

    source = inspect.getsource(blender_script_quality)
    real_bpy = [
        line.strip()
        for line in source.splitlines()
        if line.strip().startswith("import bpy") or line.strip().startswith("from bpy")
    ]
    assert real_bpy == []
    real_subprocess = [
        line.strip()
        for line in source.splitlines()
        if line.strip().startswith("import subprocess") or line.strip().startswith("from subprocess")
    ]
    assert real_subprocess == []
