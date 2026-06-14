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
        """Structure OK + scene.py conforme au template + objets runtime conformes → passed.

        H.4.8 : runtime contract validator product_render exige aussi des
        object_names runtime conformes (Backdrop_Plane, Pedestal,
        Product_Subject, Camera, Key_Light, Fill_Light).
        H.4.8.2 : avec ce object_names complet, la normalisation passive est
        déclenchée → le corrector est aussi appelé. On mock les deux
        subprocess.run (validator + corrector) avec le même side_effect."""
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

        bpy_report = _fake_bpy_report(
            object_count=6,
            light_count=2,
            object_names=[
                "Backdrop_Plane", "Pedestal", "Product_Subject",
                "Camera", "Key_Light", "Fill_Light",
            ],
        )

        side_effect = _make_proc_success(tmp_path, bpy_report)
        with patch(
            "app.engine.blender_validator.subprocess.run",
            side_effect=side_effect,
        ), patch(
            "app.engine.blender_runtime_corrector.subprocess.run",
            side_effect=side_effect,
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
        """Le rapport doit exposer template_name pour la traçabilité.

        H.4.8.2 : object_names par défaut de `_fake_bpy_report` ne contient
        pas Product_Subject → pas de normalisation déclenchée → un seul
        subprocess.run dans validator → la mock validator-only suffit."""
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
        bpy_report = _fake_bpy_report()  # default: ["Cube", "Camera", "Key_Light"]
        with patch(
            "app.engine.blender_validator.subprocess.run",
            side_effect=_make_proc_success(tmp_path, bpy_report),
        ):
            report = inspect_blend_scene(
                "blender", str(blend_path), str(tmp_path), 30,
                template_name="product_render",
            )
        assert report.get("template_name") == "product_render"


# ---------------------------------------------------------------------------
# H.4.8 — Intégration runtime_contract + correction loop
# ---------------------------------------------------------------------------

class TestRuntimeContractIntegration:
    """
    Vérifie l'orchestration H.4.8 : validator runtime + correction + ré-inspection.

    Toutes les exécutions Blender sont mockées. Les tests vérifient :
    - que runtime_contract est toujours présent dans scene_report,
    - que initial_violations / final_violations / corrections_applied sont
      conformes selon la trajectoire,
    - que la correction n'est PAS appliquée hors cas nominal,
    - que ast_guard reste signal-only (invariant H.4.7).
    """

    def _make_blend(self, tmp_path: Path) -> str:
        blend = tmp_path / "scene.blend"
        blend.write_bytes(b"FAKE")
        (tmp_path / "scene.py").write_text("import bpy\n", encoding="utf-8")
        return str(blend)

    def test_runtime_contract_present_even_without_template(self, tmp_path):
        """Sans template_name, runtime_contract doit être présent en mode skipped."""
        blend = self._make_blend(tmp_path)
        bpy_report = _fake_bpy_report()
        with patch(
            "app.engine.blender_validator.subprocess.run",
            side_effect=_make_proc_success(tmp_path, bpy_report),
        ):
            report = inspect_blend_scene("blender", blend, str(tmp_path), 30)

        assert "runtime_contract" in report
        assert report["runtime_contract"]["status"] == "skipped"
        assert report["runtime_contract"]["corrections_applied"] == []

    def test_no_correction_when_product_subject_absent(self, tmp_path):
        """Smoke H.4.7 si Product_Subject était absent — pas de correction tentée."""
        blend = self._make_blend(tmp_path)
        bpy_report = _fake_bpy_report(
            object_names=["Backdrop_Plane", "Pedestal", "Camera", "Sun"],
        )
        with patch(
            "app.engine.blender_validator.subprocess.run",
            side_effect=_make_proc_success(tmp_path, bpy_report),
        ) as mock_run:
            report = inspect_blend_scene(
                "blender", blend, str(tmp_path), 30,
                template_name="product_render",
            )

        # Une seule passe d'inspection (pas de correction → pas de seconde inspection)
        assert mock_run.call_count == 1
        rc = report["runtime_contract"]
        assert rc["correction_status"] == "skipped"
        assert rc["correction_reason"] == "no_product_subject"
        assert rc["corrections_applied"] == []
        # Les violations runtime initiales sont quand même reportées dans final_violations
        # (puisqu'aucune correction n'a été appliquée)
        assert rc["initial_violations"] == rc["final_violations"]

    def test_runtime_violations_propagated_to_scene_report(self, tmp_path):
        """Sans correction possible, runtime_contract.final_violations doit remonter
        dans scene_report.violations."""
        blend = self._make_blend(tmp_path)
        # Product_Subject absent → pas de correction, mais violations reportées
        bpy_report = _fake_bpy_report(
            object_names=["Backdrop_Plane", "Pedestal", "Camera", "Sun"],
        )
        with patch(
            "app.engine.blender_validator.subprocess.run",
            side_effect=_make_proc_success(tmp_path, bpy_report),
        ):
            report = inspect_blend_scene(
                "blender", blend, str(tmp_path), 30,
                template_name="product_render",
            )

        violations_str = " ".join(report["violations"])
        assert "template_required_missing:Product_Subject" in violations_str
        assert "template_forbidden_object:Sun" in violations_str

    def test_correction_applied_when_nominal_case(self, tmp_path):
        """Cas nominal H.4.8 : Product_Subject présent, Key/Fill manquants, Sun présent.
        La correction doit être déclenchée et la ré-inspection observer l'état corrigé."""
        blend = self._make_blend(tmp_path)

        # 1ère inspection : état smoke H.4.7
        initial_report = _fake_bpy_report(
            object_names=["Backdrop_Plane", "Pedestal", "Product_Subject",
                          "Camera", "Sun"],
            light_count=1,
        )
        # 2ème inspection (après correction) : Sun supprimé, Key_Light + Fill_Light ajoutés
        corrected_report = _fake_bpy_report(
            object_names=["Backdrop_Plane", "Pedestal", "Product_Subject",
                          "Camera", "Key_Light", "Fill_Light"],
            light_count=2,
        )

        # Trois subprocess.run successifs : inspection initiale, correction,
        # inspection finale. On distingue par le contenu du script invoqué.
        call_state = {"inspect_count": 0}

        def _side_effect(cmd, **kwargs):
            script_arg = cmd[-1]
            try:
                content = Path(script_arg).read_text(encoding="utf-8")
            except Exception:
                content = ""

            # Le script d'inspection contient `open(<report_path>, "w"`.
            # H.6.9 : le script de CORRECTION écrit aussi un fichier
            # (hero_framing.json) — on le distingue par son marqueur.
            import re
            m = re.search(r"open\((.+?),", content)
            if m and "hero_framing_v1" not in content:
                # subprocess d'inspection
                report_path = m.group(1).strip().strip("'\"")
                payload = initial_report if call_state["inspect_count"] == 0 else corrected_report
                Path(report_path).write_text(json.dumps(payload), encoding="utf-8")
                call_state["inspect_count"] += 1
            # Sinon : subprocess de correction — on ne fait rien, juste returncode=0
            result = MagicMock()
            result.returncode = 0
            result.stdout = ""
            result.stderr = ""
            return result

        with patch(
            "app.engine.blender_validator.subprocess.run",
            side_effect=_side_effect,
        ), patch(
            "app.engine.blender_runtime_corrector.subprocess.run",
            side_effect=_side_effect,
        ):
            report = inspect_blend_scene(
                "blender", blend, str(tmp_path), 60,
                template_name="product_render",
            )

        rc = report["runtime_contract"]
        # initial_violations contient les manques + Sun
        assert "template_required_missing:Key_Light" in rc["initial_violations"]
        assert "template_required_missing:Fill_Light" in rc["initial_violations"]
        assert "template_forbidden_object:Sun" in rc["initial_violations"]
        # final_violations devrait être vide après correction réussie
        assert rc["final_violations"] == []
        assert rc["correction_status"] == "applied"
        # corrections_applied non vide
        assert len(rc["corrections_applied"]) > 0
        # before / after distincts
        assert rc["before"]["object_names"] != rc["after"]["object_names"]
        assert "Sun" in rc["before"]["object_names"]
        assert "Sun" not in rc["after"]["object_names"]
        assert "Key_Light" in rc["after"]["object_names"]
        assert "Fill_Light" in rc["after"]["object_names"]
        # scene_report.violations reflète l'état final, pas l'initial
        violations_str = " ".join(report["violations"])
        assert "template_required_missing:Key_Light" not in violations_str
        assert "template_forbidden_object:Sun" not in violations_str

    def test_ast_guard_signal_only_invariant_preserved(self, tmp_path):
        """H.4.7 invariant : ast_guard.violations ne doivent JAMAIS remonter dans
        scene_report.violations, même quand H.4.8/H.4.8.2 modifient les
        violations finales (normalisation passive déclenchée ici)."""
        blend = self._make_blend(tmp_path)
        bpy_report = _fake_bpy_report(
            object_names=["Backdrop_Plane", "Pedestal", "Product_Subject",
                          "Camera", "Key_Light", "Fill_Light"],
        )
        fake_ast_guard = {
            "status": "degraded",
            "violations": ["external_asset_loaded:obj", "no_primitive_add"],
            "checks": {},
            "metrics": {},
        }
        side_effect = _make_proc_success(tmp_path, bpy_report)
        with patch(
            "app.engine.blender_validator.subprocess.run",
            side_effect=side_effect,
        ), patch(
            "app.engine.blender_runtime_corrector.subprocess.run",
            side_effect=side_effect,
        ):
            report = inspect_blend_scene(
                "blender", blend, str(tmp_path), 30,
                template_name="product_render",
                ast_guard=fake_ast_guard,
            )

        # ast_guard est exposé tel quel
        assert report["ast_guard"]["status"] == "degraded"
        assert "external_asset_loaded:obj" in report["ast_guard"]["violations"]
        # mais ses violations N'apparaissent PAS dans scene_report.violations
        assert "external_asset_loaded:obj" not in report["violations"]
        assert "no_primitive_add" not in report["violations"]

    def test_runtime_contract_skipped_for_interior_space(self, tmp_path):
        """interior_space n'est pas dans RUNTIME_CONTRACT_SPECS V0 → status skipped."""
        blend = self._make_blend(tmp_path)
        bpy_report = _fake_bpy_report(
            object_names=["Floor_Plane", "Wall_Back", "Main_Subject",
                          "Camera", "Key_Light"],
        )
        with patch(
            "app.engine.blender_validator.subprocess.run",
            side_effect=_make_proc_success(tmp_path, bpy_report),
        ):
            report = inspect_blend_scene(
                "blender", blend, str(tmp_path), 30,
                template_name="interior_space",
            )

        rc = report["runtime_contract"]
        assert rc["status"] == "skipped"
        assert rc["initial_violations"] == []
        assert rc["final_violations"] == []
        assert rc["corrections_applied"] == []

    def test_before_after_keys_present_always(self, tmp_path):
        """Schema stability : before/after toujours présents même si rien à corriger."""
        blend = self._make_blend(tmp_path)
        bpy_report = _fake_bpy_report()
        with patch(
            "app.engine.blender_validator.subprocess.run",
            side_effect=_make_proc_success(tmp_path, bpy_report),
        ):
            report = inspect_blend_scene("blender", blend, str(tmp_path), 30)

        rc = report["runtime_contract"]
        for key in ("status", "template_name", "initial_violations",
                    "final_violations", "corrections_applied",
                    "correction_status", "correction_reason",
                    "before", "after"):
            assert key in rc, f"clé manquante : {key}"


class TestRuntimeContractNormalizationH482:
    """
    H.4.8.2 — Normalisation passive runtime product_render.

    Quand le contrat est satisfait (toutes les violations runtime à []) mais
    que le cadrage peut être mauvais, la normalisation canonique doit
    s'appliquer quand même. Le runtime_contract doit refléter honnêtement
    l'action prise et ne PAS dire 'no_corrections_needed'.

    Cas de régression réel : outputs/blender/7f6d28f5-313e-4b3d-a28f-b6b9419fe28b.
    """

    def _make_blend(self, tmp_path: Path) -> str:
        """Crée scene.blend + scene.py product_render-conforme (mentions des
        required_objects statiques pour ne pas déclencher missing_required:*
        via validate_scene_py_against_template (H.4.3-C))."""
        blend = tmp_path / "scene.blend"
        blend.write_bytes(b"FAKE")
        (tmp_path / "scene.py").write_text(
            'import bpy\n'
            'obj1 = "Backdrop_Plane"\n'
            'obj2 = "Pedestal"\n'
            'obj3 = "Product_Subject"\n'
            'obj4 = "Camera"\n'
            'obj5 = "Key_Light"\n',
            encoding="utf-8",
        )
        return str(blend)

    def test_normalization_applied_when_contract_passed(self, tmp_path):
        """Cas 7f6d28f5 en miniature : tous objets contractuels présents,
        initial_violations vide, mais normalisation doit quand même
        s'appliquer et reporter honnêtement."""
        blend = self._make_blend(tmp_path)
        # Preview initial existe : permet à H.4.8.2 de capturer le mtime initial
        preview = tmp_path / "preview.png"
        preview.write_bytes(b"INITIAL_PREVIEW")

        bpy_report = _fake_bpy_report(
            object_count=6,
            light_count=2,
            object_names=["Backdrop_Plane", "Pedestal", "Product_Subject",
                          "Camera", "Key_Light", "Fill_Light"],
        )
        side_effect = _make_proc_success(tmp_path, bpy_report)
        # Mock run_visual_qa : les bytes preview du test ne sont pas un PNG
        # valide, mais on ne teste pas visual_qa ici — on teste runtime_contract.
        fake_visual_qa = {"status": "passed", "violations": [], "checks": {}}
        with patch(
            "app.engine.blender_validator.subprocess.run",
            side_effect=side_effect,
        ), patch(
            "app.engine.blender_runtime_corrector.subprocess.run",
            side_effect=side_effect,
        ), patch(
            "app.engine.blender_validator.run_visual_qa",
            return_value=fake_visual_qa,
        ):
            report = inspect_blend_scene(
                "blender", blend, str(tmp_path), 60,
                template_name="product_render",
                render_path=str(preview),
            )

        rc = report["runtime_contract"]
        # Contrat satisfait : initial_violations vide
        assert rc["initial_violations"] == []
        assert rc["final_violations"] == []
        # MAIS normalisation appliquée
        assert rc["correction_status"] == "applied"
        assert rc["correction_reason"] is None, (
            "correction_reason ne doit pas être 'no_corrections_needed' "
            f"quand une normalisation a été appliquée ; got: {rc['correction_reason']}"
        )
        # Tokens de normalisation présents
        assert "normalize_camera" in rc["corrections_applied"]
        assert "normalize_lighting" in rc["corrections_applied"]
        assert "rerender_preview" in rc["corrections_applied"]
        # Tokens correctifs absents (contrat OK)
        assert "remove_sun" not in rc["corrections_applied"]
        assert "add_key_light" not in rc["corrections_applied"]
        assert "add_fill_light" not in rc["corrections_applied"]
        # status reste passed (le contrat n'est pas violé)
        assert rc["status"] == "passed"
        assert report["status"] == "passed"
        assert report["violations"] == []

    def test_normalization_skipped_for_non_product_render_template(self, tmp_path):
        """Normalisation est exclusive à product_render (V0). interior_space
        ne doit jamais déclencher la normalisation H.4.8.2."""
        blend = self._make_blend(tmp_path)
        bpy_report = _fake_bpy_report(
            object_names=["Floor_Plane", "Wall_Back", "Wall_Left", "Wall_Right",
                          "Main_Subject", "Camera", "Key_Light"],
        )
        with patch(
            "app.engine.blender_validator.subprocess.run",
            side_effect=_make_proc_success(tmp_path, bpy_report),
        ):
            report = inspect_blend_scene(
                "blender", blend, str(tmp_path), 30,
                template_name="interior_space",
            )

        rc = report["runtime_contract"]
        assert rc["status"] == "skipped"
        assert rc["correction_status"] == "skipped"
        assert rc["corrections_applied"] == []

    def test_normalization_preview_only_exists_after_correction(self, tmp_path):
        """preview_writer_dedup — plus de rendu eager : aucune preview n'existe
        AVANT correction. Pour un product_render corrigé, le corrector est le
        SEUL writer → `before` n'a pas de preview_meta, `after` en a une."""
        blend = self._make_blend(tmp_path)
        preview = tmp_path / "preview.png"
        # NB : on ne pré-crée PAS la preview (contrairement à l'ancien flux).

        bpy_report = _fake_bpy_report(
            object_count=6,
            light_count=2,
            object_names=["Backdrop_Plane", "Pedestal", "Product_Subject",
                          "Camera", "Key_Light", "Fill_Light"],
        )

        # Le side_effect doit aussi modifier le preview pour simuler un re-rendu :
        # on écrit un contenu différent à chaque appel du corrector subprocess.
        def _side_effect_with_rerender(cmd, **kwargs):
            script_path = cmd[-1]
            try:
                content = Path(script_path).read_text(encoding="utf-8")
            except Exception:
                content = ""
            # Inspection : écrit le bpy_report dans le fichier référencé.
            # H.6.9 : le script de correction écrit aussi hero_framing.json
            # — son marqueur l'exclut de l'heuristique d'inspection.
            import re as _re
            m = _re.search(r"open\((.+?),", content)
            if m and "hero_framing_v1" not in content:
                report_path = m.group(1).strip().strip("'\"")
                Path(report_path).write_text(json.dumps(bpy_report), encoding="utf-8")
            # Correction : simule le re-rendu (taille différente)
            if "render.render" in content:
                # Petite attente artificielle pour garantir mtime différent
                import time as _time
                _time.sleep(0.05)
                preview.write_bytes(b"NEW_RENDERED_PREVIEW_BYTES_DIFFERENT_SIZE")
            result = MagicMock()
            result.returncode = 0
            result.stdout = ""
            result.stderr = ""
            return result

        with patch(
            "app.engine.blender_validator.subprocess.run",
            side_effect=_side_effect_with_rerender,
        ), patch(
            "app.engine.blender_runtime_corrector.subprocess.run",
            side_effect=_side_effect_with_rerender,
        ):
            report = inspect_blend_scene(
                "blender", blend, str(tmp_path), 60,
                template_name="product_render",
                render_path=str(preview),
            )

        rc = report["runtime_contract"]
        before = rc["before"]
        after = rc["after"]
        # before : aucune preview rendue en amont → pas de méta preview.
        assert "preview_size_bytes" not in before
        assert "preview_mtime_iso" not in before
        # after : le corrector a produit la preview → méta présente.
        assert "preview_size_bytes" in after
        assert "preview_mtime_iso" in after
        assert preview.exists()



# ---------------------------------------------------------------------------
# preview_writer_dedup — matrice « un SEUL writer » + un SEUL vrai appel QA.
# Compte explicitement : corrector (apply_corrections), renderer de base, QA réelle.
# ---------------------------------------------------------------------------

_PRODUCT_NAMES = ["Backdrop_Plane", "Pedestal", "Product_Subject",
                  "Camera", "Key_Light", "Fill_Light"]
_MALFORMED_NAMES = ["Backdrop_Plane", "Pedestal", "Camera"]  # Product_Subject absent


def _dedup_blend(tmp_path):
    blend = tmp_path / "scene.blend"
    blend.write_bytes(b"FAKE")
    return str(blend)


def _dedup_base_renderer(preview: Path, counter: dict, content: bytes = b"BASEPNG"):
    def _render():
        counter["n"] += 1
        preview.write_bytes(content)
        return str(preview)
    return _render


def _run_dedup(tmp_path, *, template, object_names, apply_mock=None,
               base_content=b"BASEPNG"):
    """Pilote inspect_blend_scene avec QA mockée (comptage) et corrector mockable."""
    blend = _dedup_blend(tmp_path)
    preview = tmp_path / "preview.png"
    bpy_report = _fake_bpy_report(
        object_count=len(object_names),
        light_count=sum(1 for n in object_names if "Light" in n),
        object_names=object_names,
    )
    base_calls = {"n": 0}
    qa_mock = MagicMock(return_value={"status": "passed", "violations": [], "checks": {}})

    patches = [
        patch("app.engine.blender_validator.subprocess.run",
              side_effect=_make_proc_success(tmp_path, bpy_report)),
        patch("app.engine.blender_validator.run_visual_qa", qa_mock),
    ]
    if apply_mock is not None:
        patches.append(patch("app.engine.blender_validator.apply_corrections", apply_mock))

    import contextlib
    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        report = inspect_blend_scene(
            "blender", blend, str(tmp_path), 60,
            template_name=template, render_path=str(preview),
            base_preview_renderer=_dedup_base_renderer(preview, base_calls, base_content),
        )
    return report, preview, base_calls, qa_mock


def test_dedup_A_product_render_corrector_sole_writer(tmp_path):
    """A. product_render valide, corrector réussi : corrector=1, base=0, QA=1."""
    apply_mock = MagicMock(side_effect=lambda **kw: (
        Path(kw["render_path"]).write_bytes(b"CORRECTED"),
        {"status": "applied",
         "corrections_applied": ["normalize_lighting", "normalize_camera", "rerender_preview"],
         "reason": None, "stderr": None})[1])
    report, preview, base_calls, qa_mock = _run_dedup(
        tmp_path, template="product_render", object_names=_PRODUCT_NAMES,
        apply_mock=apply_mock)
    assert apply_mock.call_count == 1          # corrector = 1
    assert base_calls["n"] == 0                # base = 0
    assert qa_mock.call_count == 1             # QA réelle = 1
    assert preview.read_bytes() == b"CORRECTED"
    assert report["runtime_contract"]["correction_status"] == "applied"
    assert report["runtime_contract"]["before"]["visual_qa_status"] == "skipped"


def test_dedup_B_legacy_uses_base_renderer(tmp_path):
    """B. legacy : corrector=0, base=1, QA=1."""
    apply_mock = MagicMock()  # ne doit jamais être appelé
    report, preview, base_calls, qa_mock = _run_dedup(
        tmp_path, template=None, object_names=["Cube", "Camera", "Key_Light"],
        apply_mock=apply_mock)
    assert apply_mock.call_count == 0          # corrector = 0
    assert base_calls["n"] == 1                # base = 1
    assert qa_mock.call_count == 1             # QA réelle = 1
    assert preview.exists()


def test_dedup_C_malformed_product_render_uses_base(tmp_path):
    """C. product_render malformé (Product_Subject absent) : corrector non
    applicable → corrector=0, base=1, QA=1. Distinct du corrector qui échoue."""
    apply_mock = MagicMock()  # plan non applicable → jamais appelé
    report, preview, base_calls, qa_mock = _run_dedup(
        tmp_path, template="product_render", object_names=_MALFORMED_NAMES,
        apply_mock=apply_mock)
    assert apply_mock.call_count == 0          # corrector = 0 (non applicable)
    assert base_calls["n"] == 1                # base = 1
    assert qa_mock.call_count == 1             # QA réelle = 1
    assert preview.exists()


def test_dedup_D_correction_fails_falls_back(tmp_path):
    """D. product_render applicable mais correction échouée : corrector=1
    tentative, base=1, QA=1."""
    apply_mock = MagicMock(return_value={
        "status": "error", "corrections_applied": [], "reason": "timeout", "stderr": None})
    report, preview, base_calls, qa_mock = _run_dedup(
        tmp_path, template="product_render", object_names=_PRODUCT_NAMES,
        apply_mock=apply_mock)
    assert apply_mock.call_count == 1          # corrector = 1 tentative
    assert base_calls["n"] == 1                # base = 1 (fallback)
    assert qa_mock.call_count == 1             # QA réelle = 1
    assert preview.read_bytes() == b"BASEPNG"


def test_dedup_E_corrector_empty_file_falls_back(tmp_path):
    """E. corrector produit un fichier VIDE → inexploitable → base=1 (non vide)."""
    apply_mock = MagicMock(side_effect=lambda **kw: (
        Path(kw["render_path"]).write_bytes(b""),   # fichier vide
        {"status": "applied",
         "corrections_applied": ["normalize_lighting", "normalize_camera", "rerender_preview"],
         "reason": None, "stderr": None})[1])
    report, preview, base_calls, qa_mock = _run_dedup(
        tmp_path, template="product_render", object_names=_PRODUCT_NAMES,
        apply_mock=apply_mock)
    assert apply_mock.call_count == 1          # corrector = 1 (a produit un vide)
    assert base_calls["n"] == 1                # base = 1 (preview vide inexploitable)
    assert qa_mock.call_count == 1             # QA réelle = 1 (sur la base non vide)
    assert preview.read_bytes() == b"BASEPNG"
