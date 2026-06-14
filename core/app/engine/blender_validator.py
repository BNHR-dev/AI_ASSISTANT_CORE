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
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from app.engine import framing_contract
from app.engine.blender_templates import get_template_spec
from app.engine.blender_qa_visual import _empty_checks, run_visual_qa
from app.engine.blender_runtime_contract import evaluate_runtime_contract
from app.engine.blender_runtime_corrector import apply_corrections, plan_corrections


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

    # framing_contract (§9.2) — données brutes pour la projection géométrique.
    # Isolé dans son propre try : ne doit jamais casser l'inspection structurelle.
    try:
        from mathutils import Vector
        _cam = bpy.context.scene.camera
        _subj = bpy.context.scene.objects.get("Product_Subject")
        if _cam is not None and _subj is not None:
            bpy.context.view_layer.update()  # matrix_world paresseux
            _rd = bpy.context.scene.render
            report["framing_raw"] = {{
                "camera": {{
                    "view_matrix": [list(r) for r in _cam.matrix_world.inverted()],
                    "lens": _cam.data.lens,
                    "sensor_width": _cam.data.sensor_width,
                    "sensor_height": _cam.data.sensor_height,
                    "sensor_fit": _cam.data.sensor_fit,
                    "shift_x": _cam.data.shift_x,
                    "shift_y": _cam.data.shift_y,
                }},
                "render": {{
                    "res_x": _rd.resolution_x,
                    "res_y": _rd.resolution_y,
                    "pixel_x": _rd.pixel_aspect_x,
                    "pixel_y": _rd.pixel_aspect_y,
                }},
                "subject_corners": [
                    list(_subj.matrix_world @ Vector(c)) for c in _subj.bound_box
                ],
            }}
    except Exception as e:
        report["_framing_error"] = str(e)

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


def _empty_runtime_contract_block(template_name: str | None) -> dict:
    """Bloc runtime_contract neutre — utilisé quand aucune passe applicable."""
    return {
        "status": "skipped",
        "template_name": template_name,
        "initial_violations": [],
        "final_violations": [],
        "corrections_applied": [],
        "correction_status": "skipped",
        "correction_reason": None,
        "before": {},
        "after": {},
    }


def _build_framing_block(bpy_data: dict | None, visual_qa: dict | None) -> dict:
    """
    framing_contract V1 (§9.2, Décision 17) — autorité de cadrage géométrique.
    Construit le bloc à partir des données brutes `framing_raw` du subprocess
    bpy + de la bbox perceptuelle (visual_qa) pour le signal `framing_divergence`.
    `skipped` si les données géométriques sont absentes (scène mockée, pas de
    Product_Subject, etc.). Ne lève jamais.

    Observabilité V1 : ce bloc N'ESCALADE PAS report["status"] (l'occupation
    canonique est sous la cible — cf. §9.2/B1 ; le transfert d'autorité
    décisionnelle viendra avec le recalibrage). framing_divergence = signal-only.
    """
    raw = (bpy_data or {}).get("framing_raw")
    if not raw:
        return {"status": "skipped", "violations": [], "method": framing_contract.METHOD_V1}
    try:
        cam = raw["camera"]
        rnd = raw["render"]
        corners = [tuple(c) for c in raw["subject_corners"]]
        view_matrix = tuple(tuple(row) for row in cam["view_matrix"])
        hw, hh = framing_contract.half_extents_at_unit_depth(
            cam["lens"],
            cam.get("sensor_width", 36.0),
            cam.get("sensor_height", 24.0),
            cam.get("sensor_fit", "AUTO"),
            rnd["res_x"], rnd["res_y"],
            rnd.get("pixel_x", 1.0), rnd.get("pixel_y", 1.0),
        )
        block = framing_contract.evaluate_framing(
            view_matrix, {"half_w": hw, "half_h": hh}, corners
        )
        # Divergence projeté↔perçu en fractions [0,1] (indépendant de la résolution :
        # le .blend peut être en 1920² alors que la preview est en 512²).
        perceptual_frac = None
        try:
            vq = visual_qa or {}
            size = vq.get("image_size")
            bbox = vq.get("checks", {}).get("subject_bbox_detected", {}).get("bbox")
            if size and bbox and size[0] and size[1]:
                w, h = size[0], size[1]
                perceptual_frac = [bbox[0] / w, bbox[1] / h, bbox[2] / w, bbox[3] / h]
        except Exception:
            perceptual_frac = None
        block["framing_divergence"] = framing_contract.framing_divergence(
            block.get("screen_bbox", [0, 0, 0, 0]), perceptual_frac
        )
        return block
    except Exception as e:
        return {"status": "skipped", "violations": [],
                "method": framing_contract.METHOD_V1, "details": f"framing error: {e}"}


def _preview_metadata(render_path: str | None) -> dict | None:
    """
    H.4.8.2 — Capture la taille et le mtime du preview au moment de l'appel.
    Renvoie None si le chemin est absent ou que le fichier n'existe pas.
    Best-effort : ne lève jamais d'exception.
    """
    if not render_path:
        return None
    try:
        p = Path(render_path)
        if not p.exists():
            return None
        st = p.stat()
        return {
            "preview_size_bytes": st.st_size,
            "preview_mtime_iso": datetime.fromtimestamp(
                st.st_mtime, tz=timezone.utc
            ).isoformat(),
        }
    except Exception:
        return None


def _preview_is_usable(path: "str | Path | None") -> bool:
    """
    preview_writer_dedup — une preview est exploitable seulement si elle existe,
    est un fichier et n'est pas vide. Source unique du contrat de validité,
    importée aussi par blender_client (BlenderResult.render_path).
    Best-effort : une erreur filesystem => non exploitable.
    """
    if not path:
        return False
    preview_path = Path(path)
    try:
        return preview_path.is_file() and preview_path.stat().st_size > 0
    except OSError:
        return False


def _skipped_visual_qa() -> dict:
    """
    preview_writer_dedup — structure "skipped" du contrat run_visual_qa, SANS
    lancer l'analyse. Utilisée pour l'état initial : aucune preview eager n'est
    rendue, donc la QA initiale est skipped par construction (zéro vrai appel).
    """
    return {"status": "skipped", "violations": [], "checks": _empty_checks()}


def _snapshot_state(bpy_data: dict, visual_qa: dict, preview_meta: dict | None = None) -> dict:
    """
    Capture l'état de la scène pour before/after dans runtime_contract.

    H.4.8.2 — Accepte un `preview_meta` figé à l'avance afin que `before` et
    `after` reflètent bien la métadata du preview À LEUR INSTANT respectif
    (le fichier preview.png est ré-écrit lors du re-rendu corrector, donc
    on doit avoir capturé l'état initial avant correction).
    """
    snap = {
        "object_names": list(bpy_data.get("object_names", []) or []),
        "object_count": bpy_data.get("object_count", 0),
        "visual_qa_status": visual_qa.get("status") if isinstance(visual_qa, dict) else None,
        "visual_qa_violations": list(visual_qa.get("violations", []) or []) if isinstance(visual_qa, dict) else [],
    }
    if isinstance(preview_meta, dict):
        snap.update(preview_meta)
    return snap


def _run_inspection_subprocess(
    exe: str,
    blend_path: Path,
    output_dir_path: Path,
    timeout: int,
) -> tuple[dict | None, str | None]:
    """
    Lance Blender en background pour récupérer les métriques structurelles
    via _INSPECT_SCRIPT_TEMPLATE. Helper extrait pour pouvoir être appelé
    deux fois (avant + après correction H.4.8).

    Retourne (bpy_data, error_violation_or_None).
    - (dict, None)         : succès, bpy_data contient les compteurs
    - (None, V_SUBPROCESS_ERROR)   : returncode != 0
    - (None, V_TIMEOUT)            : TimeoutExpired
    - (None, V_INVALID_REPORT_JSON): JSON produit illisible
    - (None, V_SUBPROCESS_ERROR)   : toute autre exception

    Ne lève jamais d'exception.
    """
    tmp_report_path: str | None = None
    inspect_script_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            suffix=".json", delete=False, mode="w", encoding="utf-8"
        ) as tmp_f:
            tmp_report_path = tmp_f.name
            tmp_f.write("{}")

        inspect_script = _INSPECT_SCRIPT_TEMPLATE.format(report_path=tmp_report_path)
        inspect_script_path = str(output_dir_path / "_inspect_scene.py")
        Path(inspect_script_path).write_text(inspect_script, encoding="utf-8")

        proc = subprocess.run(
            # C1b — --factory-startup + --disable-autoexec : le .blend
            # inspecté provient de code généré, ne pas exécuter ses scripts
            # embarqués ni charger les prefs/addons utilisateur.
            [exe, "--background", "--factory-startup", "--disable-autoexec",
             str(blend_path), "--python", inspect_script_path],
            capture_output=True,
            text=True,
            timeout=timeout,
        )

        if proc.returncode != 0:
            return None, V_SUBPROCESS_ERROR

        try:
            raw = Path(tmp_report_path).read_text(encoding="utf-8")
            bpy_data = json.loads(raw)
        except Exception:
            return None, V_INVALID_REPORT_JSON

        return bpy_data, None

    except subprocess.TimeoutExpired:
        return None, V_TIMEOUT
    except Exception:
        return None, V_SUBPROCESS_ERROR
    finally:
        for p in (inspect_script_path, tmp_report_path):
            if p:
                try:
                    Path(p).unlink(missing_ok=True)
                except Exception:
                    pass


def inspect_blend_scene(
    exe: str,
    output_path: str,
    output_dir: str,
    timeout: int,
    template_name: str | None = None,
    render_path: str | None = None,
    ast_guard: dict | None = None,
    base_preview_renderer: Callable[[], str | None] | None = None,
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
    base_preview_renderer : preview_writer_dedup — renderer de base injecté
                    (DI, évite l'import circulaire validator -> client). Appelé
                    en FILET DE SÉCURITÉ uniquement si, après la phase de
                    correction, aucune preview n'existe à render_path (scène
                    legacy, product_render malformé, ou correction non
                    appliquée). Pour product_render corrigé, le corrector reste
                    le seul writer et ce renderer n'est PAS appelé.

    H.4.8 — Runtime correction loop product_render :
    Après inspection initiale, si template_name == "product_render" et que
    Product_Subject est présent dans la scène, une passe corrective
    déterministe (ajout Key_Light/Fill_Light canoniques, neutralisation Sun,
    cadrage caméra canonique, re-rendu preview) peut être appliquée. La
    scène est ensuite ré-inspectée et visual_qa recalculé. Le bloc
    `runtime_contract` du rapport préserve l'état initial et final.

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

    # H.4.5 / preview_writer_dedup — Plus aucun rendu eager : aucune preview
    # n'existe à ce stade. L'état initial est "skipped" CONSTRUIT directement
    # (pas de vrai appel run_visual_qa) et aucune preview_meta. Le SEUL vrai
    # appel QA a lieu plus bas, sur la preview finale réellement produite.
    initial_visual_qa = _skipped_visual_qa()
    initial_preview_meta = None

    # H.4.7 — AST guard V0 : toujours présent dans le rapport, fallback skipped si None.
    # Signal-only : ses violations ne sont JAMAIS propagées dans report["violations"].
    ast_guard_payload = ast_guard if isinstance(ast_guard, dict) else {
        "status": "skipped",
        "violations": [],
        "checks": {},
        "metrics": {},
    }

    # H.4.8 — bloc runtime_contract initialisé en "skipped" (sera enrichi si applicable)
    runtime_contract_block = _empty_runtime_contract_block(template_name)

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
        "visual_qa": initial_visual_qa,  # H.4.5 — toujours présent
        "ast_guard": ast_guard_payload,  # H.4.7 — toujours présent
        "runtime_contract": runtime_contract_block,  # H.4.8 — toujours présent
    }

    if not blend_exists:
        violations = _determine_violations({}, blend_exists, scene_py_exists)
        violations.extend(semantic_violations)
        base_report["violations"] = violations
        base_report["status"] = "failed"
        _write_report(report_json_path, base_report)
        return base_report

    # Inspection initiale via subprocess Blender
    initial_bpy, error_violation = _run_inspection_subprocess(
        exe, blend_path, output_dir_path, inspect_timeout
    )

    if error_violation is not None:
        violations = _determine_violations({}, blend_exists, scene_py_exists)
        violations.append(error_violation)
        violations.extend(semantic_violations)
        base_report["violations"] = violations
        base_report["status"] = "failed"
        _write_report(report_json_path, base_report)
        return base_report

    # Inspection initiale réussie
    initial_bpy = initial_bpy or {}
    initial_object_names = list(initial_bpy.get("object_names", []) or [])

    # H.4.8 — Évaluation initiale du contrat runtime
    initial_runtime = evaluate_runtime_contract(initial_object_names, template_name)

    # Planification + application éventuelle de la correction
    plan = plan_corrections(
        template_name,
        initial_object_names,
        initial_runtime.get("violations", []),
    )

    final_bpy = initial_bpy
    final_visual_qa = initial_visual_qa
    final_runtime = initial_runtime
    correction_status = "skipped"
    correction_reason = plan["reason"]
    corrections_applied: list[str] = []

    if plan["applicable"] and plan["corrections"]:
        correction_result = apply_corrections(
            exe=exe,
            blend_path=str(blend_path),
            output_dir=str(output_dir_path),
            render_path=render_path,
            template_name=template_name,
            object_names=initial_object_names,
            initial_violations=initial_runtime.get("violations", []),
            timeout=timeout,
        )
        correction_status = correction_result.get("status", "error")
        correction_reason = correction_result.get("reason")
        corrections_applied = list(correction_result.get("corrections_applied", []) or [])

        if correction_status == "applied":
            # Ré-inspection après correction (timeout borné, même pattern qu'initial)
            second_bpy, second_error = _run_inspection_subprocess(
                exe, blend_path, output_dir_path, inspect_timeout
            )
            if second_error is None and second_bpy is not None:
                final_bpy = second_bpy
                # (QA visuelle recalculée UNE seule fois plus bas, sur la
                # preview finale réellement produite — pas ici.)
                final_runtime = evaluate_runtime_contract(
                    list(final_bpy.get("object_names", []) or []),
                    template_name,
                )
            # Si la ré-inspection plante, on garde l'état initial pour
            # éviter de mentir : runtime_contract.final_violations reflètera
            # alors l'état initial. correction_status="applied" reste vrai.

    # preview_writer_dedup — FILET DE SÉCURITÉ : si aucun writer n'a produit une
    # preview EXPLOITABLE (absente, non-fichier, vide, ou inaccessible), rendre
    # la preview de base via le renderer injecté. Cas couverts : legacy,
    # product_render malformé, correction non appliquée / en échec, ou corrector
    # produisant un fichier vide. Pour un product_render corrigé avec une preview
    # exploitable, le corrector reste le SEUL writer → ce bloc est sauté.
    if (
        render_path
        and base_preview_renderer is not None
        and not _preview_is_usable(render_path)
    ):
        try:
            base_preview_renderer()
        except Exception:
            pass

    # preview_writer_dedup — UNIQUE vrai appel QA, sur la preview finale réellement
    # livrée (après corrector et/ou fallback). Si rien d'exploitable, l'état reste
    # "skipped" construit plus haut (zéro appel).
    if _preview_is_usable(render_path):
        final_visual_qa = run_visual_qa(render_path)

    # Construction du bloc runtime_contract (always present)
    runtime_contract_block = {
        "status": final_runtime.get("status", "skipped"),
        "template_name": template_name,
        "initial_violations": list(initial_runtime.get("violations", []) or []),
        "final_violations": list(final_runtime.get("violations", []) or []),
        "corrections_applied": corrections_applied,
        "correction_status": correction_status,
        "correction_reason": correction_reason,
        "before": _snapshot_state(initial_bpy, initial_visual_qa, initial_preview_meta),
        "after": _snapshot_state(final_bpy, final_visual_qa, _preview_metadata(render_path)),
    }

    # Rapport final (états bpy = final, visual_qa = final)
    report = {
        **base_report,
        "object_count":      final_bpy.get("object_count", 0),
        "mesh_count":        final_bpy.get("mesh_count", 0),
        "camera_count":      final_bpy.get("camera_count", 0),
        "light_count":       final_bpy.get("light_count", 0),
        "has_active_camera": final_bpy.get("has_active_camera", False),
        "object_names":      list(final_bpy.get("object_names", []) or []),
        "visual_qa":         final_visual_qa,
        "runtime_contract":  runtime_contract_block,
        "framing_contract":  _build_framing_block(final_bpy, final_visual_qa),
    }

    # Agrégation des violations finales :
    # structural (sur l'état final) + semantic scaffold + visual_qa final + runtime_contract final.
    # NOTE : ast_guard ET framing_contract restent signal-only — leurs violations
    # NE SONT PAS ajoutées ici (Décision 17 : V1 en observabilité, autorité
    # décisionnelle de cadrage transférée après recalibrage de l'occupation).
    violations = _determine_violations(report, blend_exists, scene_py_exists)
    violations.extend(semantic_violations)
    violations.extend(final_visual_qa.get("violations", []) or [])
    violations.extend(final_runtime.get("violations", []) or [])  # H.4.8
    report["violations"] = violations
    report["status"] = _determine_status(violations)

    _write_report(report_json_path, report)
    return report


def _write_report(path: Path, report: dict) -> None:
    """Écrit scene_report.json. Ne lève pas d'exception."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
