"""
artifact_manifest.py — Manifest d'artefact Blender (H.1).

Écrit un fichier manifest.json dans outputs/blender/<request_id>/
après chaque exécution Blender. Best-effort : jamais bloquant.

Structure du manifest :
  manifest_version, pipeline, request_id, created_at,
  status (success|degraded|failed),
  input.prompt, input.task_type,
  artifacts.* (chemins + existence),
  scene_report.status, scene_report.violations,
  execution.blender_status, execution.blender_error,
  repro.{repro_version, aac_git_commit, blender_version, scene_py_sha256,
         scene_report_semantic_sha256, preview_png{sha256, dhash}},
  future.creative_intent, future.template_used, future.iteration_parent

v2 (chantier repro) : bloc `repro` — tier 1 (hash du scene.py exécuté,
version Blender, commit AAC), tier 2 (hash sémantique du scene_report,
chemins exclus — le `.blend` binaire n'est volontairement PAS hashé, il est
instable à scène identique), tier 3 (sha256 + dHash du preview.png).
Définition des tiers : app/engine/repro.py.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.engine import repro
from app.engine.blender_types import BlenderRequest, BlenderResult

MANIFEST_VERSION = 2
PIPELINE = "blender"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _artifact_entry(path: str | None) -> dict[str, Any]:
    """Retourne le chemin et l'existence d'un artefact."""
    if not path:
        return {"path": None, "exists": False}
    return {"path": path, "exists": Path(path).exists()}


def _manifest_status(blender_status: str) -> str:
    """
    Traduit le statut brut Blender en statut produit simplifié.
      success           → success
      error / timeout   → degraded  (Blender a tourné mais résultat invalide)
      no_output         → degraded
      blender_not_found → failed
    """
    if blender_status == "success":
        return "success"
    if blender_status in ("error", "timeout", "no_output"):
        return "degraded"
    return "failed"


def _resolve_scene_report(result: BlenderResult) -> dict[str, Any]:
    """
    Le scene_report effectif : BlenderResult.scene_report (champ renseigné par
    run_blender_script), avec fallback sur meta["blender_scene_report"] pour
    les appelants qui passeraient encore par meta. Dict vide sinon.
    """
    report = getattr(result, "scene_report", None)
    if isinstance(report, dict) and report:
        return report
    meta = result.meta if isinstance(getattr(result, "meta", None), dict) else {}
    fallback = meta.get("blender_scene_report")
    return fallback if isinstance(fallback, dict) else {}


def _scene_report_section(result: BlenderResult) -> dict[str, Any]:
    """Résumé scene_report du manifest. Fallback : status=unavailable, violations=[]."""
    report = _resolve_scene_report(result)
    if not report:
        return {"status": "unavailable", "violations": []}
    return {
        "status": report.get("status", "unavailable"),
        "violations": report.get("violations", []),
    }


def _repro_section(
    script_path: str | None,
    preview_path: str | None,
    scene_report: dict[str, Any],
) -> dict[str, Any]:
    """
    Bloc repro (tiers 1/2/3). Chaque champ est best-effort : un fichier
    absent (preview non rendu, script manquant) donne null, jamais d'échec.
    """
    return {
        "repro_version": repro.REPRO_VERSION,
        "aac_git_commit": repro.aac_git_commit(),
        "blender_version": repro.blender_version(),
        # BYO Ollama : endpoint + modèles actifs au moment du run (le script
        # a été GÉNÉRÉ par ce modèle — la provenance doit le dire).
        "ollama": repro.ollama_environment(),
        "scene_py_sha256": repro.sha256_file(script_path),
        "scene_report_semantic_sha256": repro.semantic_scene_report_hash(scene_report),
        "preview_png": {
            "sha256": repro.sha256_file(preview_path),
            "pixels_sha256": repro.sha256_image_pixels(preview_path),
            "dhash": repro.dhash_image(preview_path),
        },
    }


def build_blender_manifest(
    request: BlenderRequest,
    result: BlenderResult,
) -> dict[str, Any]:
    """
    Construit le dict manifest à partir de BlenderRequest et BlenderResult.
    N'écrit rien sur disque — utiliser write_blender_manifest pour ça.
    """
    output_dir = result.output_dir or request.output_dir
    base = Path(output_dir) if output_dir else None

    def _path(filename: str) -> str | None:
        return str(base / filename) if base else None

    blender_status = result.status
    manifest_status = _manifest_status(blender_status)
    scene_report_dict = _resolve_scene_report(result)

    return {
        "manifest_version": MANIFEST_VERSION,
        "pipeline": PIPELINE,
        "request_id": result.request_id,
        "created_at": _utc_now_iso(),
        "status": manifest_status,
        "output_dir": output_dir,
        "input": {
            "prompt": getattr(request, "source_prompt", None),
            "task_type": "blender_script",
        },
        "artifacts": {
            "scene_py": _artifact_entry(result.script_path or _path("scene.py")),
            "scene_blend": _artifact_entry(result.output_path or _path("scene.blend")),
            "preview_png": _artifact_entry(_path("preview.png")),
            "scene_report": _artifact_entry(_path("scene_report.json")),
            "intent_json": _artifact_entry(_path("intent.json")),
            "manifest": _artifact_entry(_path("manifest.json")),
        },
        "scene_report": _scene_report_section(result),
        "execution": {
            "blender_status": blender_status,
            "blender_error": result.error,
        },
        "repro": _repro_section(
            script_path=result.script_path or _path("scene.py"),
            preview_path=_path("preview.png"),
            scene_report=scene_report_dict,
        ),
        "future": {
            "creative_intent": getattr(request, "creative_intent", None),
            "template_used": getattr(request, "template_used", None),
            "iteration_parent": None,
            # H.5.3 — Traçabilité du chemin emprunté par build_blender_script.
            # Permet d'auditer après coup quel pipeline a produit le .blend :
            #   "product_render_ir_builder" : nouveau chemin IR + builder déterministe
            #   "legacy_llm_bpy_scaffold"   : ancien chemin LLM scaffold prompt-only
            "pipeline_path": getattr(request, "pipeline_path", "legacy_llm_bpy_scaffold"),
            "product_render_intent": getattr(request, "product_render_intent", None),
            # H.5.4.1 — Traçabilité fine du déclenchement product_render IR.
            # Permet d'expliquer pourquoi un prompt product_render retombe en legacy
            # (extraction LLM fallback, exception interne, ou simplement skipped).
            "product_render_ir_attempted": getattr(request, "product_render_ir_attempted", False),
            "product_render_extraction_status": getattr(request, "product_render_extraction_status", None),
            "product_render_extraction_reason": getattr(request, "product_render_extraction_reason", None),
            # C1a — Gate de sécurité bloquant : rapport visible même (et
            # surtout) quand l'exécution a été refusée (blocked_security).
            "security_gate": getattr(request, "security_gate", None),
        },
    }


def write_blender_manifest(
    request: BlenderRequest,
    result: BlenderResult,
) -> str | None:
    """
    Écrit manifest.json dans output_dir.
    Retourne le chemin absolu du manifest si succès, None sinon.
    Jamais bloquant : toute exception est swallowed et loggée sur stderr.
    """
    output_dir = result.output_dir or request.output_dir
    if not output_dir:
        return None

    manifest_path = Path(output_dir) / "manifest.json"

    try:
        manifest_data = build_blender_manifest(request, result)
        # Le manifest se déclare lui-même existant par construction :
        # si write_text réussit, le fichier sera présent sur disque.
        manifest_data["artifacts"]["manifest"]["exists"] = True
        manifest_path.write_text(
            json.dumps(manifest_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return str(manifest_path)
    except Exception as exc:  # noqa: BLE001
        import sys
        print(f"[artifact_manifest] write failed (non-blocking): {exc}", file=sys.stderr)
        return None
