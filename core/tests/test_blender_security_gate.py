"""
C1a/C1b — Tests du gate de sécurité bloquant (audit 2026-06-10, finding C1).

Couvre :
- analyze_security_gate : imports interdits, eval/exec/__import__/compile,
  open() hors pipeline, AST non parseable, scripts builder légitimes ;
- run_blender_script : refus d'exécution (status blocked_security) AVANT
  tout subprocess, manifest écrit avec le rapport du gate ;
- C1b : flags --factory-startup / --disable-autoexec sur les subprocess.

Aucune exécution Blender réelle : subprocess est monkeypatché.
"""
from __future__ import annotations

import json
from pathlib import Path

from app.clients import blender_client
from app.engine.blender_ast_guard import (
    SECURITY_BLOCKED_CALLS,
    SECURITY_BLOCKED_IMPORTS,
    V_SEC_AST_UNPARSEABLE,
    V_SEC_OPEN_CALL,
    analyze_security_gate,
)
from app.engine.blender_types import BlenderRequest
from app.engine.product_render_builder import build_product_render_scene_script
from app.engine.product_render_ir import ProductRenderIntent


_SAFE_SCRIPT = """\
import bpy

bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete()
bpy.ops.mesh.primitive_cube_add(size=0.12, location=(0, 0, 0.1))
bpy.ops.object.camera_add(location=(0.85, -1.2, 0.6))
bpy.context.scene.camera = bpy.context.object
bpy.ops.object.light_add(type='AREA', location=(0.5, -0.5, 1.0))
bpy.ops.wm.save_as_mainfile(filepath=OUTPUT_BLEND_PATH)
"""


# ---------------------------------------------------------------------------
# analyze_security_gate — pur
# ---------------------------------------------------------------------------

class TestAnalyzeSecurityGate:
    def test_safe_script_passes(self):
        report = analyze_security_gate(_SAFE_SCRIPT)
        assert report["status"] == "passed"
        assert report["violations"] == []

    def test_import_subprocess_is_blocking(self):
        # Test de validation proposé par l'audit (section 8) :
        # « script LLM contenant import subprocess → violation bloquante ».
        report = analyze_security_gate(_SAFE_SCRIPT + "\nimport subprocess\n")
        assert report["status"] == "blocked"
        assert "security_blocked_import:subprocess" in report["violations"]

    def test_each_blocked_import_is_detected(self):
        for module in SECURITY_BLOCKED_IMPORTS:
            report = analyze_security_gate(f"import bpy\nimport {module}\n")
            assert report["status"] == "blocked", module
            assert f"security_blocked_import:{module}" in report["violations"]

    def test_from_import_and_dotted_root_detected(self):
        report = analyze_security_gate("from os.path import join\n")
        assert report["status"] == "blocked"
        assert "security_blocked_import:os" in report["violations"]
        report2 = analyze_security_gate("import urllib.request\n")
        assert "security_blocked_import:urllib" in report2["violations"]

    def test_dynamic_exec_calls_blocked(self):
        for name in SECURITY_BLOCKED_CALLS:
            report = analyze_security_gate(f"import bpy\n{name}('print(1)')\n")
            assert report["status"] == "blocked", name
            assert f"security_dynamic_exec:{name}" in report["violations"]

    def test_open_literal_blocked(self):
        report = analyze_security_gate("data = open('/etc/passwd').read()\n")
        assert report["status"] == "blocked"
        assert V_SEC_OPEN_CALL in report["violations"]

    def test_open_variable_blocked_too(self):
        # Plus strict que le check signal-only : une variable quelconque
        # ne suffit pas, seules les variables pipeline sont tolérées.
        report = analyze_security_gate("p = 'x.txt'\nopen(p)\n")
        assert report["status"] == "blocked"

    def test_open_pipeline_var_allowed(self):
        report = analyze_security_gate("open(OUTPUT_BLEND_PATH)\n")
        assert report["status"] == "passed"

    def test_unparseable_is_blocked(self):
        report = analyze_security_gate("def broken(:\n")
        assert report["status"] == "blocked"
        assert V_SEC_AST_UNPARSEABLE in report["violations"]

    def test_empty_script_passes(self):
        assert analyze_security_gate("")["status"] == "passed"
        assert analyze_security_gate(None)["status"] == "passed"  # type: ignore[arg-type]

    def test_math_and_mathutils_allowed(self):
        # Imports légitimes courants dans les scripts bpy : ne pas bloquer.
        report = analyze_security_gate(
            "import bpy\nimport math\nimport mathutils\nimport random\n"
        )
        assert report["status"] == "passed"

    def test_builder_v0_and_v1_scripts_pass(self):
        v0 = ProductRenderIntent(
            schema_version="v0",
            subject={"kind": "bottle", "color": "amber", "material": "glass"},
            backdrop={"color": "neutral_gray"},
        )
        v1 = ProductRenderIntent(
            schema_version="v1",
            subject={"kind": "watch", "color": "cool_gray", "material": "metallic"},
            backdrop={"color": "#333333"},
            framing="close_packshot",
            pedestal={"color": "black"},
        )
        for intent in (v0, v1):
            script = build_product_render_scene_script(intent)
            report = analyze_security_gate(script)
            assert report["status"] == "passed", report


# ---------------------------------------------------------------------------
# run_blender_script — refus d'exécution
# ---------------------------------------------------------------------------

def _make_request(tmp_path: Path, security_gate: dict | None) -> BlenderRequest:
    output_dir = tmp_path / "req-test"
    output_dir.mkdir(parents=True, exist_ok=True)
    return BlenderRequest(
        request_id="req-test",
        script_content="import bpy",
        script_path=str(output_dir / "scene.py"),
        output_path=str(output_dir / "scene.blend"),
        render_path=str(output_dir / "preview.png"),
        output_dir=str(output_dir),
        timeout=10,
        security_gate=security_gate,
    )


class TestRunBlenderScriptBlocking:
    def test_blocked_gate_prevents_subprocess(self, tmp_path, monkeypatch):
        def _forbidden(*args, **kwargs):  # pragma: no cover - garde
            raise AssertionError("subprocess.run ne doit PAS être appelé")

        monkeypatch.setattr(blender_client.subprocess, "run", _forbidden)
        request = _make_request(
            tmp_path,
            {"status": "blocked", "violations": ["security_blocked_import:os"]},
        )
        result = blender_client.run_blender_script(request)
        assert result.status == "blocked_security"
        assert "security_blocked_import:os" in (result.error or "")
        # Manifest écrit malgré le refus (traçabilité).
        manifest_path = Path(request.output_dir) / "manifest.json"
        assert manifest_path.exists()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["future"]["security_gate"]["status"] == "blocked"

    def test_passed_gate_does_not_block(self, tmp_path, monkeypatch):
        # Gate passed → l'exécution continue jusqu'à la résolution de
        # l'exécutable ; on force blender_not_found pour s'arrêter là.
        monkeypatch.setattr(blender_client, "resolve_blender_exe", lambda: None)
        request = _make_request(tmp_path, {"status": "passed", "violations": []})
        result = blender_client.run_blender_script(request)
        assert result.status == "blender_not_found"

    def test_legacy_request_without_gate_unaffected(self, tmp_path, monkeypatch):
        monkeypatch.setattr(blender_client, "resolve_blender_exe", lambda: None)
        request = _make_request(tmp_path, None)
        result = blender_client.run_blender_script(request)
        assert result.status == "blender_not_found"


# ---------------------------------------------------------------------------
# C1b — flags de lancement Blender
# ---------------------------------------------------------------------------

class TestFactoryStartupFlags:
    def test_main_execution_uses_factory_startup(self, tmp_path, monkeypatch):
        captured: dict = {}

        class _Proc:
            returncode = 1
            stdout = ""
            stderr = "boom"

        def _capture(cmd, **kwargs):
            captured["cmd"] = cmd
            return _Proc()

        monkeypatch.setattr(blender_client, "resolve_blender_exe", lambda: "/usr/bin/blender")
        monkeypatch.setattr(blender_client.subprocess, "run", _capture)
        request = _make_request(tmp_path, {"status": "passed", "violations": []})
        blender_client.run_blender_script(request)
        assert "--factory-startup" in captured["cmd"]
