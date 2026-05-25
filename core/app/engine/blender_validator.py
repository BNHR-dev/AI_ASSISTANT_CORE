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
import re
import subprocess
import tempfile
import textwrap
from pathlib import Path

from app.engine.blender_templates import get_template_spec
from app.engine.blender_qa_visual import run_visual_qa


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

# Préfixes des violations sémantiques scaffold — H.4.3-C
V_MISSING_REQUIRED_PREFIX = "missing_required:"
V_FORBIDDEN_PREFIX_PREFIX = "forbidden_prefix:"


# ---------------------------------------------------------------------------
# Validation statique scene.py vs template spec — H.4.3-C
# ---------------------------------------------------------------------------

def validate_scene_py_against_template(
    scene_py_text: str,
    template_name: str | None,
) -> list[str]:
    """
    Vérifie que le contenu d'un scene.py respecte la spec déclarative
    associée à `template_name`.

    Retourne une liste de violations sémantiques :
      - "missing_required:<ObjectName>" si un nom requis n'apparaît pas
        dans le texte du script.
      - "forbidden_prefix:<Prefix>" si un identifiant commençant par un
        préfixe interdit apparaît (ex. Wall_Back pour product_render).

    Si `template_name` est None / inconnu / scene_py_text vide, retourne [].

    Fonction PURE : pas d'I/O, pas de dépendance Blender. Testable hors VM.
    """
    if not template_name or not isinstance(scene_py_text, str) or not scene_py_text:
        return []

    spec = get_template_spec(template_name)
    if spec is None:
        return []

    violations: list[str] = []

    for obj_name in spec.get("required_objects", []):
        if obj_name and obj_name not in scene_py_text:
            violations.append(f"{V_MISSING_REQUIRED_PREFIX}{obj_name}")

    for prefix in spec.get("forbidden_prefixes", []):
        if not prefix:
            continue
        # Le préfixe est interdit s'il est suivi d'un caractère d'identifiant
        # (ex. "Wall_Back", "Wall_Left"). Une mention isolée du préfixe seul
        # n'est pas un objet et n'est pas comptée comme violation.
        pattern = re.escape(prefix) + r"\w"
        if re.search(pattern, scene_py_text):
            violations.append(f"{V_FORBIDDEN_PREFIX_PREFIX}{prefix}")

    return violations


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


def _semantic_violations_from_scene_py(
    output_dir_path: Path,
    template_name: str | None,
) -> list[str]:
    """
    Lit scene.py dans output_dir si présent et calcule les violations
    sémantiques vis-à-vis de template_name. Retourne [] en cas d'absence
    de fichier, de template inconnu, ou de toute erreur de lecture.
    """
    if not template_name:
        return []
    scene_py_path = output_dir_path / "scene.py"
    if not scene_py_path.exists():
        return []
    try:
        text = scene_py_path.read_text(encoding="utf-8")
    except Exception:
        return []
    return validate_scene_py_against_template(text, template_name)


def inspect_blend_scene(
    exe: str,
    output_path: str,
    output_dir: str,
    timeout: int,
    template_name: str | None = None,
    render_path: str | None = None,
    ast_guard: dict | None = None,
) -> dict:
    """
    Inspecte un .blend produit par le pipeline Blender.

    Lance un script bpy dans Blender en background, collecte les métriques
    structurelles, écrit scene_report.json dans output_dir et retourne le rapport.

    Paramètres
    ----------
    exe           : chemin vers l'exécutable Blender
    output_path   : chemin vers le .blend à inspecter
    output_dir    : dossier de sortie (outputs/blender/<request_id>/)
    timeout       : timeout global du pipeline — borné à 30s pour rester léger
    template_name : nom du template sélectionné (H.4.3-C). Si fourni,
                    déclenche la validation statique scene.py vs spec et
                    ajoute les violations sémantiques au rapport.
    render_path   : chemin vers preview.png (H.4.5). Si fourni et existant,
                    déclenche la QA visuelle V0 (luminance, cadrage sujet).
                    visual_qa est toujours présent dans le rapport retourné.
    ast_guard     : rapport AST guard V0 (H.4.7). Signal-only : pas propagé
                    dans report["violations"]. Toujours présent dans le
                    rapport retourné, fallback "skipped" si None.

    Retourne toujours un dict, ne lève jamais d'exception.
    """
    inspect_timeout = min(timeout, 30)
    output_dir_path = Path(output_dir)
    blend_path = Path(output_path)
    report_json_path = output_dir_path / "scene_report.json"

    # Existence des artefacts attendus
    blend_exists    = blend_path.exists()
    scene_py_exists = (output_dir_path / "scene.py").exists()

    # Violations sémantiques scaffold (lecture statique scene.py — H.4.3-C)
    semantic_violations = _semantic_violations_from_scene_py(
        output_dir_path, template_name
    )

    # H.4.5 — QA visuelle V0 : exécutée une seule fois, toujours présente dans le rapport
    visual_qa = run_visual_qa(render_path)

    # H.4.7 — AST guard V0 : toujours présent dans le rapport, fallback skipped si None.
    # Signal-only : ses violations ne sont JAMAIS propagées dans report["violations"].
    ast_guard_payload = ast_guard if isinstance(ast_guard, dict) else {
        "status": "skipped",
        "violations": [],
        "checks": {},
        "metrics": {},
    }

    # Rapport de base (sera enrichi si l'inspection réussit)
    base_report: dict = {
        "scene_blend_exists": blend_exists,
        "scene_py_exists": scene_py_exists,
        "scene_report_path": str(report_json_path),
        "template_name": template_name,
        "object_count": 0,
        "mesh_count": 0,
        "camera_count": 0,
        "light_count": 0,
        "has_active_camera": False,
        "object_names": [],
        "violations": [],
        "status": "failed",
        "visual_qa": visual_qa,  # H.4.5 — toujours présent (skipped si pas de preview)
        "ast_guard": ast_guard_payload,  # H.4.7 — toujours présent (skipped si None)
    }

    if not blend_exists:
        violations = _determine_violations({}, blend_exists, scene_py_exists)
        violations.extend(semantic_violations)
        base_report["violations"] = violations
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
            violations.extend(semantic_violations)
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
            violations.extend(semantic_violations)
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
        violations.extend(semantic_violations)
        # H.4.5 — ajouter les violations visuelles critiques (dégradent le status structural)
        violations.extend(visual_qa.get("violations", []))
        report["violations"] = violations
        report["status"] = _determine_status(violations)

        _write_report(report_json_path, report)
        return report

    except subprocess.TimeoutExpired:
        violations = _determine_violations({}, blend_exists, scene_py_exists)
        violations.append(V_TIMEOUT)
        violations.extend(semantic_violations)
        base_report["violations"] = violations
        base_report["status"] = "failed"
        _write_report(report_json_path, base_report)
        return base_report

    except Exception:
        violations = _determine_violations({}, blend_exists, scene_py_exists)
        violations.append(V_SUBPROCESS_ERROR)
        violations.extend(semantic_violations)
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
