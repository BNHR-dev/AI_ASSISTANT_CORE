"""
Blender Structural Validation — inspection légère d'un .blend produit.

Après une exécution Blender réussie, lance un script bpy dans un subprocess
séparé (best-effort, même pattern que _render_preview) et produit
outputs/blender/<request_id>/scene_report.json.

Ne bloque jamais le pipeline. Retourne toujours un dict avec au minimum
{"status": "failed", "violations": [...]} en cas d'erreur.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
import textwrap
from pathlib import Path


# ---------------------------------------------------------------------------
# Violations connues — strings explicites pour les tests et la lisibilité
# ---------------------------------------------------------------------------

V_MISSING_BLEND_FILE = "missing_blend_file"
V_MISSING_SCENE_PY   = "missing_scene_py"
V_NO_OBJECTS         = "no_objects"
V_NO_MESH            = "no_mesh"
V_NO_CAMERA          = "no_camera"
V_NO_ACTIVE_CAMERA   = "no_active_camera"
V_NO_LIGHT           = "no_light"
V_SUBPROCESS_ERROR   = "subprocess_error"
V_INVALID_REPORT_JSON = "invalid_report_json"
V_TIMEOUT            = "timeout"


# ---------------------------------------------------------------------------
# Script bpy d'inspection (exécuté par Blender en background)
# ---------------------------------------------------------------------------

_INSPECT_SCRIPT_TEMPLATE = textwrap.dedent("""\
    import bpy, json, sys

    report = {{
        "object_count": 0,
        "mesh_count": 0,
        "camera_count": 0,
        "light_count": 0,
        "has_active_camera": False,
        "object_names": [],
    }}

    try:
        objs = list(bpy.context.scene.objects)
        report["object_count"] = len(objs)
        report["object_names"] = [o.name for o in objs]
        report["mesh_count"]   = sum(1 for o in objs if o.type == "MESH")
        report["camera_count"] = sum(1 for o in objs if o.type == "CAMERA")
        report["light_count"]  = sum(1 for o in objs if o.type == "LIGHT")
        report["has_active_camera"] = bpy.context.scene.camera is not None
    except Exception as e:
        report["_inspect_error"] = str(e)

    with open({report_path!r}, "w", encoding="utf-8") as f:
        json.dump(report, f)
""")


def _determine_violations(report: dict, blend_exists: bool, scene_py_exists: bool) -> list[str]:
    violations: list[str] = []
    if not blend_exists:
        violations.append(V_MISSING_BLEND_FILE)
    if not scene_py_exists:
        violations.append(V_MISSING_SCENE_PY)
    if report.get("object_count", 0) == 0:
        violations.append(V_NO_OBJECTS)
    if report.get("mesh_count", 0) == 0:
        violations.append(V_NO_MESH)
    if report.get("camera_count", 0) == 0:
        violations.append(V_NO_CAMERA)
    if not report.get("has_active_camera", False):
        violations.append(V_NO_ACTIVE_CAMERA)
    if report.get("light_count", 0) == 0:
        violations.append(V_NO_LIGHT)
    return violations


def _determine_status(violations: list[str]) -> str:
    """passed / degraded / failed selon la sévérité des violations."""
    if not violations:
        return "passed"
    # failed si le fichier .blend est absent (rien à inspecter)
    if V_MISSING_BLEND_FILE in violations or V_SUBPROCESS_ERROR in violations:
        return "failed"
    return "degraded"


def inspect_blend_scene(
    exe: str,
    output_path: str,
    output_dir: str,
    timeout: int,
) -> dict:
    """
    Inspecte un .blend produit par le pipeline Blender.

    Lance un script bpy dans Blender en background, collecte les métriques
    structurelles, écrit scene_report.json dans output_dir et retourne le rapport.

    Paramètres
    ----------
    exe         : chemin vers l'exécutable Blender
    output_path : chemin vers le .blend à inspecter
    output_dir  : dossier de sortie (outputs/blender/<request_id>/)
    timeout     : timeout global du pipeline — borné à 30s pour rester léger

    Retourne toujours un dict, ne lève jamais d'exception.
    """
    inspect_timeout = min(timeout, 30)
    output_dir_path = Path(output_dir)
    blend_path = Path(output_path)
    report_json_path = output_dir_path / "scene_report.json"

    # Existence des artefacts attendus
    blend_exists    = blend_path.exists()
    scene_py_exists = (output_dir_path / "scene.py").exists()

    # Rapport de base (sera enrichi si l'inspection réussit)
    base_report: dict = {
        "scene_blend_exists": blend_exists,
        "scene_py_exists": scene_py_exists,
        "scene_report_path": str(report_json_path),
        "object_count": 0,
        "mesh_count": 0,
        "camera_count": 0,
        "light_count": 0,
        "has_active_camera": False,
        "object_names": [],
        "violations": [],
        "status": "failed",
    }

    if not blend_exists:
        base_report["violations"] = _determine_violations({}, blend_exists, scene_py_exists)
        base_report["status"] = "failed"
        _write_report(report_json_path, base_report)
        return base_report

    # Fichier temporaire pour recevoir le JSON d'inspection
    tmp_report_path: str | None = None
    inspect_script_path: str | None = None

    try:
        # Créer un fichier temporaire pour le rapport bpy
        with tempfile.NamedTemporaryFile(
            suffix=".json", delete=False, mode="w", encoding="utf-8"
        ) as tmp_f:
            tmp_report_path = tmp_f.name
            tmp_f.write("{}")  # contenu initial vide

        # Créer le script d'inspection
        inspect_script = _INSPECT_SCRIPT_TEMPLATE.format(report_path=tmp_report_path)
        inspect_script_path = str(output_dir_path / "_inspect_scene.py")
        Path(inspect_script_path).write_text(inspect_script, encoding="utf-8")

        proc = subprocess.run(
            [exe, "--background", str(blend_path), "--python", inspect_script_path],
            capture_output=True,
            text=True,
            timeout=inspect_timeout,
        )

        if proc.returncode != 0:
            violations = _determine_violations({}, blend_exists, scene_py_exists)
            violations.append(V_SUBPROCESS_ERROR)
            base_report["violations"] = violations
            base_report["status"] = "failed"
            _write_report(report_json_path, base_report)
            return base_report

        # Lire le JSON produit par le script bpy
        try:
            raw = Path(tmp_report_path).read_text(encoding="utf-8")
            bpy_data = json.loads(raw)
        except Exception:
            violations = _determine_violations({}, blend_exists, scene_py_exists)
            violations.append(V_INVALID_REPORT_JSON)
            base_report["violations"] = violations
            base_report["status"] = "failed"
            _write_report(report_json_path, base_report)
            return base_report

        # Fusionner les données bpy dans le rapport
        report = {
            **base_report,
            "object_count":      bpy_data.get("object_count", 0),
            "mesh_count":        bpy_data.get("mesh_count", 0),
            "camera_count":      bpy_data.get("camera_count", 0),
            "light_count":       bpy_data.get("light_count", 0),
            "has_active_camera": bpy_data.get("has_active_camera", False),
            "object_names":      bpy_data.get("object_names", []),
        }

        violations = _determine_violations(report, blend_exists, scene_py_exists)
        report["violations"] = violations
        report["status"] = _determine_status(violations)

        _write_report(report_json_path, report)
        return report

    except subprocess.TimeoutExpired:
        violations = _determine_violations({}, blend_exists, scene_py_exists)
        violations.append(V_TIMEOUT)
        base_report["violations"] = violations
        base_report["status"] = "failed"
        _write_report(report_json_path, base_report)
        return base_report

    except Exception:
        violations = _determine_violations({}, blend_exists, scene_py_exists)
        violations.append(V_SUBPROCESS_ERROR)
        base_report["violations"] = violations
        base_report["status"] = "failed"
        _write_report(report_json_path, base_report)
        return base_report

    finally:
        # Nettoyage des fichiers temporaires
        for p in (inspect_script_path, tmp_report_path):
            if p:
                try:
                    Path(p).unlink(missing_ok=True)
                except Exception:
                    pass


def _write_report(path: Path, report: dict) -> None:
    """Écrit scene_report.json. Ne lève pas d'exception."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
