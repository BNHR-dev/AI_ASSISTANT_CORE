"""
reproduce.py — Rejeu d'un run depuis son manifest v2 (chantier repro, phase replay).

Consomme ce que la phase capture enregistre (bloc `repro` des manifests,
sidecars `workflow_resolved_v<i>.json`, `scene.py`) et répond à UNE question :
« si je rejoue ce run aujourd'hui, est-ce que j'obtiens la même chose ? »

Verdicts (du meilleur au pire) :
  exact       tier 3 strict : pixels identiques (pixels_sha256), ou tier 2
              strict côté Blender (hash sémantique du scene_report identique).
              Légitime en même-machine : prouvé au bit près sur la stack réelle
              (replay après restart à froid de ComfyUI, 2026-07-11).
  perceptual  pixels différents mais dHash ≤ seuil (AAC_REPRO_DHASH_THRESHOLD,
              défaut 4) — le repli cross-machine (bruit GPU).
  different   les artefacts ne correspondent pas.
  failed      le rejeu n'a pas pu produire d'artefact (service KO, timeout,
              chemins introuvables).
  refused     on REFUSE de rejouer : intégrité cassée (le contenu fourni ne
              re-hashe pas ce que le manifest enregistre) ou gate de sécurité
              bloquante sur le scene.py (C1a — un endpoint qui exécute du
              Python soumis sans gate serait un RCE).

Contraintes apprises en conditions réelles (2026-07-11) :
- ComfyUI CACHE l'exécution : re-soumettre un graphe identique ne produit
  AUCUNE image. Parade double : POST /free (best-effort) + réécriture du
  filename_prefix du nœud de sauvegarde vers repro/<orig>/<stamp> — ce qui
  isole aussi les sorties du rejeu (le run original reste intact).
- Comparer les PIXELS (pixels_sha256), jamais le sha du fichier : ComfyUI
  embarque le prompt dans les métadonnées PNG.
- Le scene.py stocké embarque les chemins de sortie ORIGINAUX en littéraux :
  le rejeu retarget le répertoire d'origine vers un répertoire neuf, et
  refuse de courir s'il ne le trouve pas (fail-closed : jamais d'écrasement
  du run original).

Imports clients (ComfyUI, Blender) TARDIFS : engine ne dépend de clients
qu'à l'exécution, jamais à l'import (même règle que comfyui_manifest).
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Optional
from uuid import uuid4

from app.engine import repro
from app.engine.blender_ast_guard import analyze_security_gate

DHASH_THRESHOLD_ENV = "AAC_REPRO_DHASH_THRESHOLD"
DEFAULT_DHASH_THRESHOLD = 4

VERDICT_EXACT = "exact"
VERDICT_PERCEPTUAL = "perceptual"
VERDICT_DIFFERENT = "different"
VERDICT_FAILED = "failed"
VERDICT_REFUSED = "refused"

# Ordre de gravité pour agréger un verdict global (le pire gagne).
_VERDICT_SEVERITY = [
    VERDICT_EXACT,
    VERDICT_PERCEPTUAL,
    VERDICT_DIFFERENT,
    VERDICT_FAILED,
    VERDICT_REFUSED,
]

REPORT_FILENAME = "reproduce_report.json"


def get_dhash_threshold() -> int:
    raw = os.environ.get(DHASH_THRESHOLD_ENV)
    if raw is None or not raw.strip():
        return DEFAULT_DHASH_THRESHOLD
    try:
        return max(0, int(raw.strip()))
    except ValueError:
        return DEFAULT_DHASH_THRESHOLD


def _worst_verdict(verdicts: list[str]) -> str:
    if not verdicts:
        return VERDICT_FAILED
    return max(verdicts, key=_VERDICT_SEVERITY.index)


def _image_verdict(
    expected: dict[str, Any] | None,
    actual_path: str | None,
    threshold: int,
) -> dict[str, Any]:
    """
    Compare une image rejouée à l'enregistrement du manifest.
    pixels_sha256 identique → exact ; sinon dHash ≤ seuil → perceptual ;
    sinon different. Manifest sans pixels_sha256 (ne devrait pas arriver
    en v2) → dHash seul.
    """
    if not expected or not actual_path:
        return {"verdict": VERDICT_FAILED, "reason": "missing image on one side"}

    actual_pixels = repro.sha256_image_pixels(actual_path)
    actual_dhash = repro.dhash_image(actual_path)
    distance = repro.dhash_distance(actual_dhash, expected.get("dhash"))

    check: dict[str, Any] = {
        "expected_pixels_sha256": expected.get("pixels_sha256"),
        "actual_pixels_sha256": actual_pixels,
        "expected_dhash": expected.get("dhash"),
        "actual_dhash": actual_dhash,
        "dhash_distance": distance,
    }

    if expected.get("pixels_sha256") and actual_pixels == expected["pixels_sha256"]:
        check["verdict"] = VERDICT_EXACT
    elif distance is not None and distance <= threshold:
        check["verdict"] = VERDICT_PERCEPTUAL
    else:
        check["verdict"] = VERDICT_DIFFERENT
    return check


def _write_report(directory: str | Path | None, report: dict[str, Any]) -> Optional[str]:
    """Persiste le rapport dans le dossier du rejeu. Best-effort."""
    if not directory:
        return None
    try:
        path = Path(directory) / REPORT_FILENAME
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return str(path)
    except OSError as exc:
        print(f"[reproduce] report write failed (non-blocking): {exc}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# ComfyUI
# ---------------------------------------------------------------------------

def _rewrite_filename_prefix(workflow: dict[str, Any], new_prefix: str) -> dict[str, Any]:
    """
    Copie du workflow avec le(s) filename_prefix retargetés. Seul champ
    modifié : il n'influe pas sur les pixels, il buste le cache du nœud de
    sauvegarde et isole les sorties du rejeu.
    """
    rewritten = json.loads(json.dumps(workflow))
    for node in rewritten.values():
        inputs = node.get("inputs") if isinstance(node, dict) else None
        if isinstance(inputs, dict) and "filename_prefix" in inputs:
            inputs["filename_prefix"] = new_prefix
    return rewritten


def _comfyui_environment_diffs(manifest_repro: dict[str, Any]) -> list[dict[str, Any]]:
    """Écarts d'environnement entre l'enregistrement et maintenant — la
    réponse à « pourquoi ça diffère ? » quand le verdict n'est pas exact."""
    from app.clients.comfyui_client import get_comfyui_system_info

    diffs: list[dict[str, Any]] = _repro_version_diff(manifest_repro)
    recorded = manifest_repro.get("comfyui") or {}
    current = get_comfyui_system_info() or {}
    for key in ("comfyui_version", "pytorch_version"):
        if recorded.get(key) and current.get(key) and recorded[key] != current[key]:
            diffs.append({"field": key, "recorded": recorded[key], "current": current[key]})

    models_dir = repro.comfyui_models_dir()
    if models_dir:
        for subdir, entries in (manifest_repro.get("models") or {}).items():
            for entry in entries or []:
                recorded_sha = entry.get("sha256")
                if not recorded_sha:
                    continue
                current_sha = repro.model_file_sha256(models_dir / subdir / entry["name"])
                if current_sha and current_sha != recorded_sha:
                    diffs.append(
                        {
                            "field": f"model:{subdir}/{entry['name']}",
                            "recorded": recorded_sha,
                            "current": current_sha,
                        }
                    )
    return diffs


def reproduce_comfyui(
    manifest: dict[str, Any],
    workflows: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    """
    Rejoue les variantes d'un run ComfyUI depuis leurs workflows résolus.

    `workflows` : index de variante (1-based, celui du manifest) → contenu du
    sidecar. L'intégrité de CHAQUE workflow est vérifiée contre le hash du
    manifest avant tout rejeu — on ne rejoue pas ce qu'on ne peut pas
    authentifier.
    """
    from app.clients.comfyui_client import (
        COMFYUI_OUTPUT_DIR,
        extract_output_file,
        free_execution_cache,
        queue_prompt,
        wait_for_completion,
    )

    started = perf_counter()
    threshold = get_dhash_threshold()
    manifest_repro = manifest.get("repro") or {}
    recorded_variants = {v.get("index"): v for v in manifest_repro.get("variants") or []}
    orig_request_id = manifest.get("request_id") or "unknown"
    stamp = uuid4().hex[:8]

    # Même contrainte de droits que run_comfyui_workflow : pré-créer le dossier
    # du rejeu AVANT que ComfyUI (conteneur root) n'y écrive l'image — sinon le
    # dossier naît root sur le volume partagé et le backend ne peut plus y
    # écrire reproduce_report.json (bug attrapé live 2026-07-11). Best-effort.
    if COMFYUI_OUTPUT_DIR:
        try:
            Path(COMFYUI_OUTPUT_DIR, "repro", orig_request_id, stamp).mkdir(
                parents=True, exist_ok=True
            )
        except OSError:
            pass

    variant_reports: list[dict[str, Any]] = []
    report_dir: Optional[str] = None

    for index in sorted(workflows):
        workflow = workflows[index]
        recorded = recorded_variants.get(index)
        entry: dict[str, Any] = {"index": index}

        if recorded is None:
            entry.update(verdict=VERDICT_REFUSED, reason="variant not present in manifest")
            variant_reports.append(entry)
            continue

        # Intégrité : le sidecar fourni doit re-hasher ce que le manifest enregistre.
        actual_hash = repro.sha256_canonical_json(workflow)
        if actual_hash != recorded.get("workflow_sha256"):
            entry.update(
                verdict=VERDICT_REFUSED,
                reason="workflow integrity mismatch",
                expected_workflow_sha256=recorded.get("workflow_sha256"),
                actual_workflow_sha256=actual_hash,
            )
            variant_reports.append(entry)
            continue

        # Contrainte cache (mesurée live) : /free + filename_prefix neuf,
        # sinon ComfyUI sert son cache et ne produit RIEN.
        new_prefix = f"repro/{orig_request_id}/{stamp}/v{index}"
        replay_workflow = _rewrite_filename_prefix(workflow, new_prefix)
        free_execution_cache()

        try:
            prompt_id = queue_prompt(replay_workflow)
            history = wait_for_completion(prompt_id)
            _filename, output_path = extract_output_file(history)
        except Exception as exc:  # noqa: BLE001 — verdict, pas crash
            entry.update(verdict=VERDICT_FAILED, reason=f"replay failed: {exc}")
            variant_reports.append(entry)
            continue

        if not output_path:
            entry.update(
                verdict=VERDICT_FAILED,
                reason="replay completed but produced no output (execution cache?)",
                prompt_id=prompt_id,
            )
            variant_reports.append(entry)
            continue

        if report_dir is None:
            report_dir = str(Path(output_path).parent)

        check = _image_verdict(recorded.get("image"), output_path, threshold)
        entry.update(
            verdict=check.pop("verdict"),
            prompt_id=prompt_id,
            output_path=output_path,
            image=check,
        )
        variant_reports.append(entry)

    report = {
        "pipeline": "comfyui",
        "reproduced_request_id": orig_request_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "verdict": _worst_verdict([v["verdict"] for v in variant_reports]),
        "dhash_threshold": threshold,
        "variants": variant_reports,
        "environment_diffs": _comfyui_environment_diffs(manifest_repro),
        "duration_ms": max(0, int((perf_counter() - started) * 1000)),
    }
    report["report_path"] = _write_report(report_dir, report)
    return report


# ---------------------------------------------------------------------------
# Blender
# ---------------------------------------------------------------------------

def reproduce_blender(manifest: dict[str, Any], scene_py: str) -> dict[str, Any]:
    """
    Ré-exécute le scene.py d'un run Blender dans un répertoire NEUF et
    compare tier 2 (hash sémantique du scene_report — le juge principal)
    et tier 3 (preview, best-effort comme le contrat preview lui-même).

    Ordre des refus : intégrité d'abord (le contenu fourni doit re-hasher
    l'enregistrement du manifest), gate de sécurité ensuite (C1a — même un
    script « authentique » reste re-audité avant exécution), retarget
    fail-closed enfin (jamais d'écrasement du run original).
    """
    from app.clients.blender_client import BLENDER_OUTPUT_DIR, run_blender_script
    from app.engine.blender_types import BlenderRequest

    started = perf_counter()
    threshold = get_dhash_threshold()
    manifest_repro = manifest.get("repro") or {}
    checks: list[dict[str, Any]] = []

    def _report(verdict: str, *, error: Optional[str] = None, run_dir: Optional[str] = None) -> dict[str, Any]:
        report = {
            "pipeline": "blender",
            "reproduced_request_id": manifest.get("request_id"),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "verdict": verdict,
            "dhash_threshold": threshold,
            "checks": checks,
            "environment_diffs": _blender_environment_diffs(manifest_repro),
            "error": error,
            "duration_ms": max(0, int((perf_counter() - started) * 1000)),
        }
        report["report_path"] = _write_report(run_dir, report)
        return report

    # 1. Intégrité du scene.py fourni.
    actual_script_hash = repro.sha256_text(scene_py)
    recorded_script_hash = manifest_repro.get("scene_py_sha256")
    if not recorded_script_hash or actual_script_hash != recorded_script_hash:
        checks.append(
            {
                "name": "scene_py_integrity",
                "verdict": VERDICT_REFUSED,
                "expected": recorded_script_hash,
                "actual": actual_script_hash,
            }
        )
        return _report(VERDICT_REFUSED, error="scene.py integrity mismatch")

    # 2. Gate de sécurité C1a — re-auditée à CHAQUE rejeu.
    gate = analyze_security_gate(scene_py)
    if gate.get("status") == "blocked":
        checks.append(
            {"name": "security_gate", "verdict": VERDICT_REFUSED, "violations": gate.get("violations")}
        )
        return _report(VERDICT_REFUSED, error="security gate blocked replay")

    # 3. Retarget fail-closed des chemins de sortie.
    orig_output_dir = manifest.get("output_dir")
    if not orig_output_dir or orig_output_dir not in scene_py:
        return _report(
            VERDICT_FAILED,
            error="original output_dir not found in scene.py — refusing to run "
            "(replay could overwrite the original run)",
        )

    repro_id = f"repro-{uuid4().hex[:8]}"
    new_dir = str(Path(BLENDER_OUTPUT_DIR) / repro_id)
    retargeted = scene_py.replace(orig_output_dir, new_dir)

    # run_blender_script exécute `--python script_path` : c'est build_blender_script
    # qui écrit ce fichier dans le flux normal — au rejeu, c'est à nous de le faire.
    script_path = Path(new_dir) / "scene.py"
    try:
        script_path.parent.mkdir(parents=True, exist_ok=True)
        script_path.write_text(retargeted, encoding="utf-8")
    except OSError as exc:
        return _report(VERDICT_FAILED, error=f"cannot write replay scene.py: {exc}")

    # template_used pilote le correcteur runtime (normalize_lighting/camera,
    # re-render du preview) : sans lui, le rejeu n'exécute PAS le même calcul
    # que l'original (mesuré live 2026-07-11 : preview divergent, corrections
    # sautées « template_not_product_render »).
    manifest_future = manifest.get("future") or {}
    request = BlenderRequest(
        request_id=repro_id,
        script_content=retargeted,
        script_path=str(script_path),
        output_path=str(Path(new_dir) / "scene.blend"),
        render_path=str(Path(new_dir) / "preview.png"),
        output_dir=new_dir,
        timeout=int(os.getenv("BLENDER_TIMEOUT", "60")),
        source_prompt=(manifest.get("input") or {}).get("prompt"),
        template_used=manifest_future.get("template_used"),
        pipeline_path="reproduce_replay",
        security_gate=gate,
    )
    result = run_blender_script(request)

    if result.status != "success":
        checks.append({"name": "blender_run", "verdict": VERDICT_FAILED, "status": result.status})
        return _report(VERDICT_FAILED, error=result.error or f"blender status: {result.status}", run_dir=new_dir)

    # 4. Tier 2 — le juge principal : même scène ⇒ même hash sémantique.
    actual_semantic = repro.semantic_scene_report_hash(result.scene_report)
    expected_semantic = manifest_repro.get("scene_report_semantic_sha256")
    semantic_verdict = (
        VERDICT_EXACT
        if expected_semantic and actual_semantic == expected_semantic
        else VERDICT_DIFFERENT
    )
    checks.append(
        {
            "name": "scene_report_semantic",
            "verdict": semantic_verdict,
            "expected": expected_semantic,
            "actual": actual_semantic,
        }
    )

    # 5. Tier 3 — preview, best-effort (le preview est non-bloquant par contrat).
    expected_preview = manifest_repro.get("preview_png") or {}
    new_preview = Path(new_dir) / "preview.png"
    if expected_preview.get("pixels_sha256") and new_preview.exists():
        check = _image_verdict(expected_preview, str(new_preview), threshold)
        check["name"] = "preview_png"
        checks.append(check)
    else:
        checks.append({"name": "preview_png", "verdict": "skipped", "reason": "preview absent on one side"})

    decisive = [c["verdict"] for c in checks if c["verdict"] in _VERDICT_SEVERITY]
    return _report(_worst_verdict(decisive), run_dir=new_dir)


def _repro_version_diff(manifest_repro: dict[str, Any]) -> list[dict[str, Any]]:
    """Un bloc repro d'une autre version n'est pas comparable terme à terme
    (l'algorithme du hash sémantique a changé en v2) — l'écart doit être
    visible dans le rapport plutôt que de laisser croire à une régression."""
    recorded = manifest_repro.get("repro_version")
    if recorded is not None and recorded != repro.REPRO_VERSION:
        return [
            {"field": "repro_version", "recorded": recorded, "current": repro.REPRO_VERSION}
        ]
    return []


def _blender_environment_diffs(manifest_repro: dict[str, Any]) -> list[dict[str, Any]]:
    diffs: list[dict[str, Any]] = _repro_version_diff(manifest_repro)
    for field, current in (
        ("blender_version", repro.blender_version()),
        ("aac_git_commit", repro.aac_git_commit()),
    ):
        recorded = manifest_repro.get(field)
        if recorded and current and recorded != current:
            diffs.append({"field": field, "recorded": recorded, "current": current})
    return diffs


# ---------------------------------------------------------------------------
# Dispatch (surface API)
# ---------------------------------------------------------------------------

def reproduce_run(
    pipeline: str,
    manifest: dict[str, Any],
    *,
    workflows: dict[int, dict[str, Any]] | None = None,
    scene_py: Optional[str] = None,
) -> dict[str, Any]:
    """Point d'entrée unique du handler API. Valide le matériel fourni."""
    if pipeline == "comfyui":
        if not workflows:
            return {
                "pipeline": "comfyui",
                "verdict": VERDICT_REFUSED,
                "error": "no resolved workflow sidecars provided",
                "variants": [],
                "environment_diffs": [],
                "dhash_threshold": get_dhash_threshold(),
            }
        return reproduce_comfyui(manifest, workflows)
    if pipeline == "blender":
        if not scene_py:
            return {
                "pipeline": "blender",
                "verdict": VERDICT_REFUSED,
                "error": "no scene.py provided",
                "checks": [],
                "environment_diffs": [],
                "dhash_threshold": get_dhash_threshold(),
            }
        return reproduce_blender(manifest, scene_py)
    raise ValueError(f"Unknown pipeline: {pipeline}")
