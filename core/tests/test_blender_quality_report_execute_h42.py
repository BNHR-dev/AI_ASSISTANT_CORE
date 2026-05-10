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
    from unittest.mock import MagicMock
    from app.engine.blender_types import BlenderRequest, BlenderResult
    from app.engine.executor import execute_request

    # "créer un cube blender" route maintenant vers blender_pipeline.
    # On mocke build_blender_script et run_blender_script pour simuler
    # un pipeline complet, et generate_with_ollama pour la quality gate.
    fake_request_id = "test-quality-001"
    fake_output_dir = f"outputs/blender/{fake_request_id}"
    fake_request = BlenderRequest(
        request_id=fake_request_id,
        script_content=_GOOD_SCRIPT,
        script_path=f"{fake_output_dir}/scene.py",
        output_path=f"{fake_output_dir}/scene.blend",
        render_path=f"{fake_output_dir}/preview.png",
        output_dir=fake_output_dir,
        timeout=60,
    )
    fake_result = BlenderResult(
        status="success",
        request_id=fake_request_id,
        script_path=f"{fake_output_dir}/scene.py",
        output_path=f"{fake_output_dir}/scene.blend",
        render_path=None,
        output_dir=fake_output_dir,
        returncode=0,
        stdout=_GOOD_SCRIPT,
        stderr="",
        error=None,
    )

    monkeypatch.setattr(
        "app.engine.step_executor.build_blender_script",
        lambda msg, ctx, rid: fake_request,
    )
    monkeypatch.setattr(
        "app.engine.step_executor.run_blender_script",
        lambda req: fake_result,
    )
    monkeypatch.setattr(
        "app.engine.step_executor.generate_with_ollama",
        lambda model, prompt: _GOOD_SCRIPT,
    )

    result = execute_request("créer un cube blender")

    assert result["output"]
    assert "blender_quality_report" in result
    # La quality gate analyse le message + final_output.
    # Avec blender_pipeline le final_output contient le texte du tool_blender.
    # La quality gate détecte "blender" dans le message donc is_blender=True.
    assert result["blender_quality_report"] is not None
    assert result["blender_quality_report"]["is_blender"] is True


def test_execute_request_bad_blender_script_has_violations(monkeypatch):
    from app.engine.blender_types import BlenderRequest, BlenderResult
    from app.engine.executor import execute_request

    fake_request_id = "test-quality-002"
    fake_output_dir = f"outputs/blender/{fake_request_id}"
    fake_request = BlenderRequest(
        request_id=fake_request_id,
        script_content=_BAD_SCRIPT,
        script_path=f"{fake_output_dir}/scene.py",
        output_path=f"{fake_output_dir}/scene.blend",
        render_path=f"{fake_output_dir}/preview.png",
        output_dir=fake_output_dir,
        timeout=60,
    )
    fake_result = BlenderResult(
        status="success",
        request_id=fake_request_id,
        script_path=f"{fake_output_dir}/scene.py",
        output_path=f"{fake_output_dir}/scene.blend",
        render_path=None,
        output_dir=fake_output_dir,
        returncode=0,
        stdout=_BAD_SCRIPT,
        stderr="",
        error=None,
    )

    monkeypatch.setattr(
        "app.engine.step_executor.build_blender_script",
        lambda msg, ctx, rid: fake_request,
    )
    monkeypatch.setattr(
        "app.engine.step_executor.run_blender_script",
        lambda req: fake_result,
    )
    monkeypatch.setattr(
        "app.engine.step_executor.generate_with_ollama",
        lambda model, prompt: _BAD_SCRIPT,
    )

    result = execute_request("créer un cube blender")

    assert "blender_quality_report" in result
    report = result["blender_quality_report"]
    assert report is not None
    assert report["is_blender"] is True


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
