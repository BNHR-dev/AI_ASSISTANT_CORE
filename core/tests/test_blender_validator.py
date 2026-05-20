"""
Tests unitaires — blender_validator.inspect_blend_scene.

Tous les tests utilisent des mocks : aucun Blender réel requis.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.engine.blender_validator import (
    V_MISSING_BLEND_FILE,
    V_MISSING_SCENE_PY,
    V_NO_CAMERA,
    V_NO_ACTIVE_CAMERA,
    V_NO_MESH,
    V_NO_OBJECTS,
    V_NO_LIGHT,
    V_SUBPROCESS_ERROR,
    V_INVALID_REPORT_JSON,
    V_TIMEOUT,
    inspect_blend_scene,
    validate_scene_py_against_template,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_bpy_report(
    object_count: int = 3,
    mesh_count: int = 1,
    camera_count: int = 1,
    light_count: int = 1,
    has_active_camera: bool = True,
    object_names: list | None = None,
) -> dict:
    return {
        "object_count": object_count,
        "mesh_count": mesh_count,
        "camera_count": camera_count,
        "light_count": light_count,
        "has_active_camera": has_active_camera,
        "object_names": object_names or ["Cube", "Camera", "Key_Light"],
    }


def _make_proc_success(tmp_path: Path, bpy_report: dict):
    """
    Retourne un mock subprocess.CompletedProcess qui écrit bpy_report
    dans le fichier temporaire passé au script d'inspection.

    On mocke subprocess.run avec un side_effect qui écrit dans le fichier JSON
    attendu par inspect_blend_scene (passé via le script .py).
    """
    def _run_side_effect(cmd, **kwargs):
        # Le script d'inspection écrit dans un fichier temporaire.
        # On retrouve le chemin du script d'inspection dans cmd[-1],
        # puis on lit le contenu du script pour extraire le chemin du rapport.
        script_path = cmd[-1]
        try:
            script_content = Path(script_path).read_text(encoding="utf-8")
            # Extraire le chemin du rapport JSON depuis le script
            import re
            m = re.search(r"open\((.+?),", script_content)
            if m:
                report_path = m.group(1).strip().strip("'\"")
                Path(report_path).write_text(
                    json.dumps(bpy_report), encoding="utf-8"
                )
        except Exception:
            pass
        result = MagicMock()
        result.returncode = 0
        result.stdout = ""
        result.stderr = ""
        return result
    return _run_side_effect


# ---------------------------------------------------------------------------
# Test : blend absent → failed + missing_blend_file
# ---------------------------------------------------------------------------

def test_inspect_returns_failed_when_blend_missing(tmp_path):
    fake_blend = str(tmp_path / "scene.blend")  # n'existe pas
    report = inspect_blend_scene("blender", fake_blend, str(tmp_path), 30)
    assert report["status"] == "failed"
    assert V_MISSING_BLEND_FILE in report["violations"]


# ---------------------------------------------------------------------------
# Test : subprocess returncode != 0 → failed + subprocess_error
# ---------------------------------------------------------------------------

def test_inspect_returns_failed_on_subprocess_error(tmp_path):
    blend_path = tmp_path / "scene.blend"
    blend_path.write_bytes(b"FAKE")

    failing_proc = MagicMock()
    failing_proc.returncode = 1
    failing_proc.stdout = ""
    failing_proc.stderr = "Error"

    with patch("app.engine.blender_validator.subprocess.run", return_value=failing_proc):
        report = inspect_blend_scene("blender", str(blend_path), str(tmp_path), 30)

    assert report["status"] == "failed"
    assert V_SUBPROCESS_ERROR in report["violations"]


# ---------------------------------------------------------------------------
# Test : TimeoutExpired → failed + timeout
# ---------------------------------------------------------------------------

def test_inspect_returns_failed_on_timeout(tmp_path):
    blend_path = tmp_path / "scene.blend"
    blend_path.write_bytes(b"FAKE")

    with patch(
        "app.engine.blender_validator.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="blender", timeout=30),
    ):
        report = inspect_blend_scene("blender", str(blend_path), str(tmp_path), 30)

    assert report["status"] == "failed"
    assert V_TIMEOUT in report["violations"]


# ---------------------------------------------------------------------------
# Test : JSON invalide produit par le script bpy → failed + invalid_report_json
# ---------------------------------------------------------------------------

def test_inspect_returns_failed_on_invalid_json(tmp_path):
    blend_path = tmp_path / "scene.blend"
    blend_path.write_bytes(b"FAKE")

    def _bad_json_run(cmd, **kwargs):
        # Écrire du JSON invalide dans le fichier temporaire
        script_path = cmd[-1]
        try:
            import re
            content = Path(script_path).read_text(encoding="utf-8")
            m = re.search(r"open\((.+?),", content)
            if m:
                report_path = m.group(1).strip().strip("'\"")
                Path(report_path).write_text("NOT_JSON{{{", encoding="utf-8")
        except Exception:
            pass
        result = MagicMock()
        result.returncode = 0
        return result

    with patch("app.engine.blender_validator.subprocess.run", side_effect=_bad_json_run):
        report = inspect_blend_scene("blender", str(blend_path), str(tmp_path), 30)

    assert report["status"] == "failed"
    assert V_INVALID_REPORT_JSON in report["violations"]


# ---------------------------------------------------------------------------
# Test : scène minimale valide → passed, pas de violations
# ---------------------------------------------------------------------------

def test_inspect_returns_passed_for_minimal_scene(tmp_path):
    blend_path = tmp_path / "scene.blend"
    blend_path.write_bytes(b"FAKE")
    (tmp_path / "scene.py").write_text("# scene", encoding="utf-8")

    bpy_report = _fake_bpy_report()

    with patch(
        "app.engine.blender_validator.subprocess.run",
        side_effect=_make_proc_success(tmp_path, bpy_report),
    ):
        report = inspect_blend_scene("blender", str(blend_path), str(tmp_path), 30)

    assert report["status"] == "passed"
    assert report["violations"] == []
    assert report["object_count"] == 3
    assert report["mesh_count"] == 1
    assert report["camera_count"] == 1
    assert report["light_count"] == 1
    assert report["has_active_camera"] is True


# ---------------------------------------------------------------------------
# Test : pas de caméra → no_camera + no_active_camera dans violations
# ---------------------------------------------------------------------------

def test_inspect_detects_no_camera_violation(tmp_path):
    blend_path = tmp_path / "scene.blend"
    blend_path.write_bytes(b"FAKE")

    bpy_report = _fake_bpy_report(
        camera_count=0,
        has_active_camera=False,
        object_names=["Cube", "Key_Light"],
    )

    with patch(
        "app.engine.blender_validator.subprocess.run",
        side_effect=_make_proc_success(tmp_path, bpy_report),
    ):
        report = inspect_blend_scene("blender", str(blend_path), str(tmp_path), 30)

    assert report["status"] == "degraded"
    assert V_NO_CAMERA in report["violations"]
    assert V_NO_ACTIVE_CAMERA in report["violations"]


# ---------------------------------------------------------------------------
# Test : pas de mesh → no_mesh dans violations
# ---------------------------------------------------------------------------

def test_inspect_detects_no_mesh_violation(tmp_path):
    blend_path = tmp_path / "scene.blend"
    blend_path.write_bytes(b"FAKE")

    bpy_report = _fake_bpy_report(
        mesh_count=0,
        object_names=["Camera", "Key_Light"],
    )

    with patch(
        "app.engine.blender_validator.subprocess.run",
        side_effect=_make_proc_success(tmp_path, bpy_report),
    ):
        report = inspect_blend_scene("blender", str(blend_path), str(tmp_path), 30)

    assert report["status"] == "degraded"
    assert V_NO_MESH in report["violations"]


# ---------------------------------------------------------------------------
# Test : pas de lumière → no_light dans violations
# ---------------------------------------------------------------------------

def test_inspect_detects_no_light_violation(tmp_path):
    blend_path = tmp_path / "scene.blend"
    blend_path.write_bytes(b"FAKE")

    bpy_report = _fake_bpy_report(light_count=0, object_names=["Cube", "Camera"])

    with patch(
        "app.engine.blender_validator.subprocess.run",
        side_effect=_make_proc_success(tmp_path, bpy_report),
    ):
        report = inspect_blend_scene("blender", str(blend_path), str(tmp_path), 30)

    assert report["status"] == "degraded"
    assert V_NO_LIGHT in report["violations"]


# ---------------------------------------------------------------------------
# Test : scène vide → no_objects + no_mesh + no_camera + no_active_camera + no_light
# ---------------------------------------------------------------------------

def test_inspect_detects_empty_scene(tmp_path):
    blend_path = tmp_path / "scene.blend"
    blend_path.write_bytes(b"FAKE")

    bpy_report = _fake_bpy_report(
        object_count=0,
        mesh_count=0,
        camera_count=0,
        light_count=0,
        has_active_camera=False,
        object_names=[],
    )

    with patch(
        "app.engine.blender_validator.subprocess.run",
        side_effect=_make_proc_success(tmp_path, bpy_report),
    ):
        report = inspect_blend_scene("blender", str(blend_path), str(tmp_path), 30)

    assert V_NO_OBJECTS in report["violations"]
    assert V_NO_MESH in report["violations"]
    assert V_NO_CAMERA in report["violations"]
    assert V_NO_ACTIVE_CAMERA in report["violations"]
    assert V_NO_LIGHT in report["violations"]


# ---------------------------------------------------------------------------
# Test : scene_report.json écrit dans output_dir
# ---------------------------------------------------------------------------

def test_inspect_writes_scene_report_json(tmp_path):
    blend_path = tmp_path / "scene.blend"
    blend_path.write_bytes(b"FAKE")

    bpy_report = _fake_bpy_report()

    with patch(
        "app.engine.blender_validator.subprocess.run",
        side_effect=_make_proc_success(tmp_path, bpy_report),
    ):
        report = inspect_blend_scene("blender", str(blend_path), str(tmp_path), 30)

    report_json = tmp_path / "scene_report.json"
    assert report_json.exists(), "scene_report.json doit être écrit dans output_dir"

    written = json.loads(report_json.read_text(encoding="utf-8"))
    assert written["status"] == report["status"]


# ---------------------------------------------------------------------------
# Test : scene_report.json est écrit même quand blend absent
# ---------------------------------------------------------------------------

def test_inspect_writes_scene_report_json_even_when_blend_missing(tmp_path):
    fake_blend = str(tmp_path / "scene.blend")  # n'existe pas
    inspect_blend_scene("blender", fake_blend, str(tmp_path), 30)

    report_json = tmp_path / "scene_report.json"
    assert report_json.exists(), "scene_report.json doit être écrit même si .blend absent"
    written = json.loads(report_json.read_text(encoding="utf-8"))
    assert written["status"] == "failed"


# ---------------------------------------------------------------------------
# Test : timeout borné à 30 même si timeout pipeline > 30
# ---------------------------------------------------------------------------

def test_inspect_timeout_is_bounded_to_30(tmp_path):
    blend_path = tmp_path / "scene.blend"
    blend_path.write_bytes(b"FAKE")

    calls = []

    def _capture_timeout(cmd, **kwargs):
        calls.append(kwargs.get("timeout"))
        result = MagicMock()
        result.returncode = 1
        return result

    with patch("app.engine.blender_validator.subprocess.run", side_effect=_capture_timeout):
        inspect_blend_scene("blender", str(blend_path), str(tmp_path), timeout=120)

    assert calls, "subprocess.run doit avoir été appelé"
    assert calls[0] <= 30, f"timeout devrait être ≤ 30, got {calls[0]}"


# ---------------------------------------------------------------------------
# Test : missing_scene_py reporté si scene.py absent
# ---------------------------------------------------------------------------

def test_inspect_detects_missing_scene_py(tmp_path):
    blend_path = tmp_path / "scene.blend"
    blend_path.write_bytes(b"FAKE")
    # Pas de scene.py dans tmp_path

    bpy_report = _fake_bpy_report()

    with patch(
        "app.engine.blender_validator.subprocess.run",
        side_effect=_make_proc_success(tmp_path, bpy_report),
    ):
        report = inspect_blend_scene("blender", str(blend_path), str(tmp_path), 30)

    assert V_MISSING_SCENE_PY in report["violations"]
    # Le statut reste degraded (pas failed — le .blend est là et lisible)
    assert report["status"] == "degraded"


# ---------------------------------------------------------------------------
# Test : scene_report_path exposé dans le rapport
# ---------------------------------------------------------------------------

def test_inspect_exposes_scene_report_path(tmp_path):
    blend_path = tmp_path / "scene.blend"
    blend_path.write_bytes(b"FAKE")

    bpy_report = _fake_bpy_report()

    with patch(
        "app.engine.blender_validator.subprocess.run",
        side_effect=_make_proc_success(tmp_path, bpy_report),
    ):
        report = inspect_blend_scene("blender", str(blend_path), str(tmp_path), 30)

    assert "scene_report_path" in report
    assert report["scene_report_path"].endswith("scene_report.json")


# ---------------------------------------------------------------------------
# H.4.3-C — Validation statique scene.py vs template spec
# ---------------------------------------------------------------------------
# La fonction pure validate_scene_py_against_template() est testable hors VM
# et sans Blender. Elle alimente les violations sémantiques scaffold dans
# inspect_blend_scene() — testé séparément ci-dessous.
# ---------------------------------------------------------------------------


class TestValidateScenePyAgainstTemplate:

    # -- product_render : violations sémantiques attendues ------------------

    def test_product_render_with_wall_back_yields_forbidden_prefix(self):
        scene_py = (
            "import bpy\n"
            'bpy.ops.mesh.primitive_cube_add()\n'
            'wall = bpy.context.object\n'
            'wall.name = "Wall_Back"\n'
            # Présence des required pour isoler la violation forbidden_prefix
            '# Backdrop_Plane Pedestal Product_Subject Camera Key_Light\n'
        )
        violations = validate_scene_py_against_template(scene_py, "product_render")
        assert "forbidden_prefix:Wall_" in violations

    def test_product_render_missing_pedestal_yields_missing_required(self):
        scene_py = (
            "import bpy\n"
            '# Backdrop_Plane Product_Subject Camera Key_Light présents\n'
            'obj1 = "Backdrop_Plane"\n'
            'obj2 = "Product_Subject"\n'
            'obj3 = "Camera"\n'
            'obj4 = "Key_Light"\n'
        )
        violations = validate_scene_py_against_template(scene_py, "product_render")
        assert "missing_required:Pedestal" in violations

    def test_product_render_missing_backdrop_yields_missing_required(self):
        scene_py = (
            "import bpy\n"
            'obj1 = "Pedestal"\n'
            'obj2 = "Product_Subject"\n'
            'obj3 = "Camera"\n'
            'obj4 = "Key_Light"\n'
        )
        violations = validate_scene_py_against_template(scene_py, "product_render")
        assert "missing_required:Backdrop_Plane" in violations

    def test_product_render_fully_compliant_returns_no_violation(self):
        scene_py = (
            "import bpy\n"
            'obj1 = "Backdrop_Plane"\n'
            'obj2 = "Pedestal"\n'
            'obj3 = "Product_Subject"\n'
            'obj4 = "Camera"\n'
            'obj5 = "Key_Light"\n'
        )
        violations = validate_scene_py_against_template(scene_py, "product_render")
        assert violations == []

    # -- interior_space : violations sémantiques attendues ------------------

    def test_interior_space_missing_wall_back_yields_missing_required(self):
        scene_py = (
            "import bpy\n"
            'obj1 = "Floor_Plane"\n'
            'obj2 = "Wall_Left"\n'
            'obj3 = "Wall_Right"\n'
            'obj4 = "Main_Subject"\n'
            'obj5 = "Camera"\n'
            'obj6 = "Key_Light"\n'
        )
        violations = validate_scene_py_against_template(scene_py, "interior_space")
        assert "missing_required:Wall_Back" in violations

    def test_interior_space_fully_compliant_returns_no_violation(self):
        scene_py = (
            "import bpy\n"
            'obj1 = "Floor_Plane"\n'
            'obj2 = "Wall_Back"\n'
            'obj3 = "Wall_Left"\n'
            'obj4 = "Wall_Right"\n'
            'obj5 = "Main_Subject"\n'
            'obj6 = "Camera"\n'
            'obj7 = "Key_Light"\n'
        )
        violations = validate_scene_py_against_template(scene_py, "interior_space")
        assert violations == []

    def test_interior_space_has_no_forbidden_prefix_rule(self):
        """Aucun préfixe interdit pour interior_space dans cette phase."""
        scene_py = (
            "import bpy\n"
            'obj1 = "Floor_Plane"\n'
            'obj2 = "Wall_Back"\n'
            'obj3 = "Wall_Left"\n'
            'obj4 = "Wall_Right"\n'
            'obj5 = "Main_Subject"\n'
            'obj6 = "Camera"\n'
            'obj7 = "Key_Light"\n'
            # Préfixe arbitraire qui ne doit générer aucune violation
            'extra = "Pedestal_X"\n'
        )
        violations = validate_scene_py_against_template(scene_py, "interior_space")
        assert not any(v.startswith("forbidden_prefix:") for v in violations)

    # -- template None / inconnu / texte vide ------------------------------

    def test_template_none_returns_no_violation(self):
        scene_py = 'import bpy\n# pas de Backdrop_Plane ni Wall_*\n'
        assert validate_scene_py_against_template(scene_py, None) == []

    def test_unknown_template_returns_no_violation(self):
        scene_py = 'import bpy\n# template inconnu\n'
        assert validate_scene_py_against_template(scene_py, "not_a_template") == []

    def test_empty_scene_py_returns_no_violation(self):
        assert validate_scene_py_against_template("", "product_render") == []
        assert validate_scene_py_against_template(None, "product_render") == []  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# H.4.3-C — Intégration : inspect_blend_scene avec template_name
# ---------------------------------------------------------------------------


class TestInspectBlendSceneSemanticIntegration:

    def _write_scene_py(self, tmp_path: Path, content: str) -> None:
        (tmp_path / "scene.py").write_text(content, encoding="utf-8")

    def test_blend_ok_with_semantic_violation_yields_degraded(self, tmp_path):
        """Structure Blender OK mais scene.py viole le template :
        status == 'degraded' et violation sémantique présente."""
        blend_path = tmp_path / "scene.blend"
        blend_path.write_bytes(b"FAKE")

        # scene.py incompatible avec product_render (manque Pedestal)
        self._write_scene_py(
            tmp_path,
            'import bpy\n'
            'obj1 = "Backdrop_Plane"\n'
            'obj2 = "Product_Subject"\n'
            'obj3 = "Camera"\n'
            'obj4 = "Key_Light"\n',
        )

        bpy_report = _fake_bpy_report()

        with patch(
            "app.engine.blender_validator.subprocess.run",
            side_effect=_make_proc_success(tmp_path, bpy_report),
        ):
            report = inspect_blend_scene(
                "blender",
                str(blend_path),
                str(tmp_path),
                30,
                template_name="product_render",
            )

        assert report["status"] == "degraded", (
            f"Une violation sémantique seule doit donner degraded, "
            f"got {report['status']} with violations={report['violations']}"
        )
        assert "missing_required:Pedestal" in report["violations"]

    def test_blend_ok_with_forbidden_prefix_yields_degraded(self, tmp_path):
        """product_render + Wall_Back dans scene.py → degraded + forbidden_prefix:Wall_."""
        blend_path = tmp_path / "scene.blend"
        blend_path.write_bytes(b"FAKE")

        self._write_scene_py(
            tmp_path,
            'import bpy\n'
            'obj1 = "Backdrop_Plane"\n'
            'obj2 = "Pedestal"\n'
            'obj3 = "Product_Subject"\n'
            'obj4 = "Camera"\n'
            'obj5 = "Key_Light"\n'
            'wall = "Wall_Back"\n',
        )

        bpy_report = _fake_bpy_report()

        with patch(
            "app.engine.blender_validator.subprocess.run",
            side_effect=_make_proc_success(tmp_path, bpy_report),
        ):
            report = inspect_blend_scene(
                "blender",
                str(blend_path),
                str(tmp_path),
                30,
                template_name="product_render",
            )

        assert report["status"] == "degraded"
        assert "forbidden_prefix:Wall_" in report["violations"]

    def test_blend_ok_fully_compliant_template_yields_passed(self, tmp_path):
        """Structure OK + scene.py conforme au template → passed."""
        blend_path = tmp_path / "scene.blend"
        blend_path.write_bytes(b"FAKE")

        self._write_scene_py(
            tmp_path,
            'import bpy\n'
            'obj1 = "Backdrop_Plane"\n'
            'obj2 = "Pedestal"\n'
            'obj3 = "Product_Subject"\n'
            'obj4 = "Camera"\n'
            'obj5 = "Key_Light"\n',
        )

        bpy_report = _fake_bpy_report()

        with patch(
            "app.engine.blender_validator.subprocess.run",
            side_effect=_make_proc_success(tmp_path, bpy_report),
        ):
            report = inspect_blend_scene(
                "blender",
                str(blend_path),
                str(tmp_path),
                30,
                template_name="product_render",
            )

        assert report["status"] == "passed"
        assert report["violations"] == []

    def test_blend_missing_keeps_failed_even_with_template(self, tmp_path):
        """blend absent reste 'failed' : la sémantique scaffold ne masque pas
        l'absence du fichier .blend."""
        fake_blend = str(tmp_path / "scene.blend")  # n'existe pas

        # scene.py présent mais incompatible — semantic violations s'ajoutent
        # mais le status reste failed à cause du missing_blend_file.
        self._write_scene_py(tmp_path, 'import bpy\n# vide\n')

        report = inspect_blend_scene(
            "blender", fake_blend, str(tmp_path), 30,
            template_name="product_render",
        )
        assert report["status"] == "failed"
        assert V_MISSING_BLEND_FILE in report["violations"]

    def test_template_name_propagated_in_report(self, tmp_path):
        """Le rapport doit exposer template_name pour la traçabilité."""
        blend_path = tmp_path / "scene.blend"
        blend_path.write_bytes(b"FAKE")

        self._write_scene_py(
            tmp_path,
            'import bpy\n'
            'obj1 = "Backdrop_Plane"\n'
            'obj2 = "Pedestal"\n'
            'obj3 = "Product_Subject"\n'
            'obj4 = "Camera"\n'
            'obj5 = "Key_Light"\n',
        )
        bpy_report = _fake_bpy_report()
        with patch(
            "app.engine.blender_validator.subprocess.run",
            side_effect=_make_proc_success(tmp_path, bpy_report),
        ):
            report = inspect_blend_scene(
                "blender", str(blend_path), str(tmp_path), 30,
                template_name="product_render",
            )
        assert report.get("template_name") == "product_render"
