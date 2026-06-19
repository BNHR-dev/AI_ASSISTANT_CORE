"""
comfyui_manifest.py — Registre d'exécution ComfyUI (analogue du manifest Blender, H.1).

Écrit un fichier `<base>.manifest.json` dans le dossier de sortie ComfyUI après
chaque génération (best-effort : jamais bloquant). Pensé pour tourner à l'identique
dans les deux OS cibles — Linux natif ET conteneur Docker (Windows/macOS via WSL2) :
le bloc `runtime` enregistre le contexte d'exécution réel, et `host_os` permet au
lanceur de stamper l'OS hôte (le conteneur, lui, est toujours Linux).

Structure :
  manifest_version, pipeline, request_id, created_at, status, output_dir,
  runtime.{os, os_release, platform, host_os, in_container, hostname, python},
  input.{prompt, negative_prompt, task_type, workflow_id, quality, template_id, parameters},
  route[ {step, type, status, started_at, finished_at, duration_ms} ... ],
  timing.{started_at, finished_at, duration_ms},
  artifacts.{images[ {path, exists, filename, view_url} ], manifest},
  execution.{comfyui_status, prompt_id, variant_prompt_ids, variant_seeds,
             variants_count, completed_variants, partial, error, run_errors}
"""
from __future__ import annotations

import json
import os
import platform
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MANIFEST_VERSION = 1
PIPELINE = "comfyui"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _artifact_entry(path: str | None) -> dict[str, Any]:
    if not path:
        return {"path": None, "exists": False}
    return {"path": path, "exists": Path(path).exists()}


def _maybe_chown_tree(root: Path) -> None:
    """Best-effort : redonne le dossier du run (image écrite par ComfyUI-root + ce
    manifest) à l'UID hôte. Sans ça, les fichiers seraient root sur le bind mount.
    No-op si AAC_OUTPUT_UID non posé (ex. en natif, où le backend est déjà l'utilisateur)."""
    raw_uid = os.getenv("AAC_OUTPUT_UID")
    if not raw_uid:
        return
    try:
        uid = int(raw_uid)
        gid = int(os.getenv("AAC_OUTPUT_GID") or raw_uid)
    except ValueError:
        return
    try:
        os.chown(root, uid, gid)
        for dirpath, dirnames, filenames in os.walk(root):
            for name in dirnames + filenames:
                try:
                    os.chown(os.path.join(dirpath, name), uid, gid)
                except OSError:
                    pass
    except OSError:
        pass


def _manifest_status(comfyui_status: str | None, partial: bool) -> str:
    """success → success ; succès partiel (variantes) → degraded ; sinon → error."""
    if comfyui_status == "success":
        return "degraded" if partial else "success"
    return "error"


def _runtime_section() -> dict[str, Any]:
    """
    Contexte d'exécution — la clé du « dans les deux OS ». Le backend tourne dans un
    conteneur Linux même sur Windows (WSL2), donc platform.system() y vaut « Linux » ;
    `host_os` (env AAC_HOST_OS, posé par le lanceur) nomme l'OS hôte réel, repli sinon.
    """
    detected_os = platform.system()
    return {
        "os": detected_os,
        "os_release": platform.release(),
        "platform": platform.platform(),
        "host_os": os.getenv("AAC_HOST_OS") or detected_os,
        "in_container": Path("/.dockerenv").exists() or os.getenv("AAC_IN_CONTAINER") == "1",
        "hostname": socket.gethostname(),
        "python": platform.python_version(),
    }


def _images_section(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Liste des images produites (chemin + existence + nom + URL /view)."""
    paths = result.get("output_paths") or ([result["output_path"]] if result.get("output_path") else [])
    names = result.get("filenames") or ([result["filename"]] if result.get("filename") else [])
    urls = result.get("artifact_view_urls") or (
        [result["artifact_view_url"]] if result.get("artifact_view_url") else []
    )
    images: list[dict[str, Any]] = []
    for idx, path in enumerate(paths):
        entry = _artifact_entry(path)
        entry["filename"] = names[idx] if idx < len(names) else (Path(path).name if path else None)
        entry["view_url"] = urls[idx] if idx < len(urls) else None
        images.append(entry)
    return images


def build_comfyui_manifest(
    request_id: str,
    result: dict[str, Any],
    *,
    timing: dict[str, Any],
    route: list[dict[str, Any]],
    output_dir: str,
) -> dict[str, Any]:
    """Construit le dict registre. N'écrit rien — voir write_comfyui_manifest."""
    params = result.get("parameters") if isinstance(result.get("parameters"), dict) else {}
    partial = bool(result.get("partial"))
    comfyui_status = result.get("status")

    return {
        "manifest_version": MANIFEST_VERSION,
        "pipeline": PIPELINE,
        "request_id": request_id,
        "created_at": _utc_now_iso(),
        "status": _manifest_status(comfyui_status, partial),
        "output_dir": output_dir,
        "runtime": _runtime_section(),
        "input": {
            "prompt": params.get("positive_prompt"),
            "negative_prompt": params.get("negative_prompt"),
            "task_type": "image_generation",
            "workflow_id": result.get("workflow_id") or params.get("workflow_id"),
            "quality": params.get("quality"),
            "template_id": params.get("template_id"),
            "parameters": params,
        },
        "route": route,
        "timing": timing,
        "artifacts": {"images": _images_section(result)},
        "execution": {
            "comfyui_status": comfyui_status,
            "prompt_id": result.get("prompt_id"),
            "variant_prompt_ids": result.get("variant_prompt_ids"),
            "variant_seeds": result.get("variant_seeds"),
            "variants_count": result.get("variants_count"),
            "completed_variants": result.get("completed_variants"),
            "partial": partial,
            "error": result.get("error"),
            "run_errors": result.get("run_errors"),
        },
    }


def write_comfyui_manifest(
    request_id: str,
    result: dict[str, Any],
    *,
    output_dir: str | None,
    timing: dict[str, Any],
    route: list[dict[str, Any]],
) -> str | None:
    """
    Écrit le registre JSON dans output_dir (repli : dossier de l'image produite).
    Retourne le chemin absolu si succès, None sinon. Jamais bloquant : toute
    exception est swallowed et loggée sur stderr.
    """
    if not output_dir:
        primary = result.get("output_path")
        output_dir = str(Path(primary).parent) if primary else None
    if not output_dir:
        return None

    manifest_path = Path(output_dir) / "manifest.json"

    try:
        data = build_comfyui_manifest(
            request_id, result, timing=timing, route=route, output_dir=output_dir
        )
        # Le manifest se déclare existant par construction (write_text réussit -> présent).
        data["artifacts"]["manifest"] = {"path": str(manifest_path), "exists": True}
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        # Le dossier du run (image ComfyUI-root + ce manifest) -> rendu à l'UID hôte.
        _maybe_chown_tree(manifest_path.parent)
        return str(manifest_path)
    except Exception as exc:  # noqa: BLE001
        print(f"[comfyui_manifest] write failed (non-blocking): {exc}", file=sys.stderr)
        return None
