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
             variants_count, completed_variants, partial, error, run_errors},
  repro.{repro_version, aac_git_commit, comfyui{versions}, models{name+sha256},
         variants[ {index, seed, prompt_id, workflow_sha256, workflow_file,
                    image{filename, sha256, dhash}} ]}

v2 (chantier repro) : bloc `repro` tier 1 (workflow résolu tel qu'envoyé —
sidecar `workflow_resolved_v<i>.json` par variante + hash canonique, noms et
sha best-effort des modèles, versions serveur, commit AAC) et tier 3 (sha256
exact + dHash perceptuel par image). Voir app/engine/repro.py pour la
définition des tiers.
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

from app.engine import repro

MANIFEST_VERSION = 2
PIPELINE = "comfyui"

WORKFLOW_SIDECAR_TEMPLATE = "workflow_resolved_v{index}.json"

# Clé d'input de nœud ComfyUI → sous-dossier modèles correspondant.
_MODEL_INPUT_SUBDIRS = {"ckpt_name": "checkpoints", "model_name": "upscale_models"}


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


def _runs_from_result(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Entrées par variante (raw_response.runs), telles qu'assemblées par
    run_comfyui_workflow. Liste vide si absentes (mocks, anciens appelants)."""
    raw = result.get("raw_response")
    runs = raw.get("runs") if isinstance(raw, dict) else None
    return [run for run in runs if isinstance(run, dict)] if isinstance(runs, list) else []


def _model_names_by_subdir(runs: list[dict[str, Any]]) -> dict[str, set[str]]:
    """Noms de modèles réellement injectés, extraits des workflows résolus
    (source de vérité de ce qui a été envoyé — pas l'env, qui peut avoir
    changé depuis)."""
    names: dict[str, set[str]] = {subdir: set() for subdir in _MODEL_INPUT_SUBDIRS.values()}
    for run in runs:
        workflow = run.get("workflow_resolved")
        if not isinstance(workflow, dict):
            continue
        for node in workflow.values():
            inputs = node.get("inputs") if isinstance(node, dict) else None
            if not isinstance(inputs, dict):
                continue
            for input_key, subdir in _MODEL_INPUT_SUBDIRS.items():
                value = inputs.get(input_key)
                if isinstance(value, str) and value:
                    names[subdir].add(value)
    return names


def _models_section(runs: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Modèles utilisés, avec sha256 best-effort (COMFYUI_MODELS_DIR lisible —
    en Docker le volume n'est pas monté côté backend → sha null, noms seuls)."""
    models_dir = repro.comfyui_models_dir()
    section: dict[str, list[dict[str, Any]]] = {}
    for subdir, names in _model_names_by_subdir(runs).items():
        entries = []
        for name in sorted(names):
            sha = repro.model_file_sha256(models_dir / subdir / name) if models_dir else None
            entries.append({"name": name, "sha256": sha})
        section[subdir] = entries
    return section


def _variant_entries(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Une entrée repro par variante : seed effectif, hash canonique du
    workflow envoyé, hashes tier 3 de l'image produite (si produite)."""
    entries: list[dict[str, Any]] = []
    for index, run in enumerate(runs, start=1):
        workflow = run.get("workflow_resolved")
        output_path = run.get("output_path")
        entries.append(
            {
                "index": index,
                "seed": run.get("seed"),
                "prompt_id": run.get("prompt_id"),
                "workflow_sha256": (
                    repro.sha256_canonical_json(workflow) if isinstance(workflow, dict) else None
                ),
                # Renseigné par write_comfyui_manifest (sidecar écrit sur disque).
                "workflow_file": None,
                "image": (
                    {
                        "filename": run.get("filename"),
                        "sha256": repro.sha256_file(output_path),
                        # ComfyUI embarque le prompt dans le PNG : les octets
                        # divergent à pixels identiques → comparer pixels_sha256.
                        "pixels_sha256": repro.sha256_image_pixels(output_path),
                        "dhash": repro.dhash_image(output_path),
                    }
                    if output_path
                    else None
                ),
            }
        )
    return entries


def _repro_section(
    result: dict[str, Any],
    comfyui_system_info: dict[str, Any] | None,
) -> dict[str, Any]:
    runs = _runs_from_result(result)
    return {
        "repro_version": repro.REPRO_VERSION,
        "aac_git_commit": repro.aac_git_commit(),
        "comfyui": comfyui_system_info,
        "models": _models_section(runs),
        "variants": _variant_entries(runs),
    }


def build_comfyui_manifest(
    request_id: str,
    result: dict[str, Any],
    *,
    timing: dict[str, Any],
    route: list[dict[str, Any]],
    output_dir: str,
    comfyui_system_info: dict[str, Any] | None = None,
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
        "repro": _repro_section(result, comfyui_system_info),
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
    # Repli sur le dossier de l'image. Garde-fou : on n'écrit QUE dans un chemin
    # absolu — un chemin relatif polluerait le répertoire courant (mocks de tests,
    # ou prod sans COMFYUI_OUTPUT_DIR). En prod réel, COMFYUI_OUTPUT_DIR est absolu.
    if not output_dir:
        primary = result.get("output_path")
        output_dir = str(Path(primary).parent) if primary else None
    if not output_dir or not os.path.isabs(output_dir):
        return None

    manifest_path = Path(output_dir) / "manifest.json"

    try:
        # Sonde versions ComfyUI (HTTP, best-effort). Import tardif : engine ne
        # dépend de clients qu'à l'exécution, jamais à l'import (layering).
        try:
            from app.clients.comfyui_client import get_comfyui_system_info

            comfyui_system_info = get_comfyui_system_info()
        except Exception:  # noqa: BLE001
            comfyui_system_info = None

        data = build_comfyui_manifest(
            request_id,
            result,
            timing=timing,
            route=route,
            output_dir=output_dir,
            comfyui_system_info=comfyui_system_info,
        )
        # Le manifest se déclare existant par construction (write_text réussit -> présent).
        data["artifacts"]["manifest"] = {"path": str(manifest_path), "exists": True}
        manifest_path.parent.mkdir(parents=True, exist_ok=True)

        # Sidecars repro : le workflow résolu de chaque variante, tel qu'envoyé
        # à ComfyUI. C'est LE fichier que `aac reproduce` rejouera — le hash
        # seul ne permet pas de rejouer. Best-effort par variante.
        runs = _runs_from_result(result)
        for entry in data["repro"]["variants"]:
            run_index = entry["index"]
            workflow = (
                runs[run_index - 1].get("workflow_resolved")
                if run_index - 1 < len(runs)
                else None
            )
            if not isinstance(workflow, dict):
                continue
            sidecar_name = WORKFLOW_SIDECAR_TEMPLATE.format(index=run_index)
            try:
                (manifest_path.parent / sidecar_name).write_text(
                    json.dumps(workflow, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                entry["workflow_file"] = sidecar_name
            except OSError:
                pass
        manifest_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        # Le dossier du run (image ComfyUI-root + ce manifest) -> rendu à l'UID hôte.
        _maybe_chown_tree(manifest_path.parent)
        return str(manifest_path)
    except Exception as exc:  # noqa: BLE001
        print(f"[comfyui_manifest] write failed (non-blocking): {exc}", file=sys.stderr)
        return None
