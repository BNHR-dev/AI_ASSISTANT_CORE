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
  future.creative_intent, future.template_used, future.iteration_parent
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.engine.blender_types import BlenderRequest, BlenderResult

MANIFEST_VERSION = 1
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


def _scene_report_section(result: BlenderResult) -> dict[str, Any]:
    """
    Extrait les infos scene_report depuis BlenderResult.meta si disponibles.
    Fallback : status=unavailable, violations=[].
    """
    meta = result.meta if isinstance(getattr(result, "meta", None), dict) else {}
    report = meta.get("blender_scene_report") or {}
    if not report:
        return {"status": "unavailable", "violations": []}
    return {
        "status": report.get("status", "unavailable"),
        "violations": report.get("violations", []),
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
        "future": {
            "creative_intent": getattr(request, "creative_intent", None),
            "template_used": getattr(request, "template_used", None),
            "iteration_parent": None,
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
