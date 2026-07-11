"""
Tier LIVE — la suite qui touche la vraie stack (chantier durcissement n°7).

Exécute les pipelines réels de bout en bout : Ollama (LLM), ComfyUI (GPU),
Blender local — puis REJOUE ce qui vient d'être produit via le moteur
reproduce. C'est le filet que la CI hermétique ne peut pas tendre : les
1700+ tests unitaires vérifient les contrats internes, ce fichier vérifie
que la réalité (formats d'API ComfyUI, comportement bpy, modèles) n'a pas
bougé sous nos pieds.

Ne tourne QUE via AAC_LIVE_TESTS=1 (lanceur : scripts/linux/live-tests.sh,
qui résout les IPs des conteneurs et pose l'environnement). Chaque test
skippe proprement si son service est injoignable. Coût total ≈ 3-5 min
(générations GPU réelles) — pensé pour un run quotidien/avant-release,
pas pour la boucle TDD.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import requests

from app.engine import repro
from app.engine.reproduce import (
    VERDICT_EXACT,
    VERDICT_PERCEPTUAL,
    reproduce_blender,
    reproduce_comfyui,
)

pytestmark = pytest.mark.live


def _reachable(url: str | None) -> bool:
    if not url:
        return False
    try:
        return requests.get(url, timeout=5).ok
    except requests.RequestException:
        return False


def _blender_exe() -> str | None:
    from app.clients.blender_client import resolve_blender_exe

    return resolve_blender_exe()


OLLAMA_OK = _reachable(os.getenv("OLLAMA_TAGS_URL"))
COMFYUI_OK = _reachable(
    f"{os.getenv('COMFYUI_URL', '').rstrip('/')}/system_stats"
    if os.getenv("COMFYUI_URL")
    else None
)

needs_ollama = pytest.mark.skipif(not OLLAMA_OK, reason="Ollama injoignable (OLLAMA_TAGS_URL)")
needs_comfyui = pytest.mark.skipif(not COMFYUI_OK, reason="ComfyUI injoignable (COMFYUI_URL)")
needs_blender = pytest.mark.skipif(_blender_exe() is None, reason="Blender local introuvable")


# ---------------------------------------------------------------------------
# LLM — routing + génération réels
# ---------------------------------------------------------------------------

@needs_ollama
def test_explain_end_to_end() -> None:
    from app.engine.executor import execute_request

    result = execute_request("explique en une phrase ce qu'est un moteur de rendu")

    assert result["execution_summary"]["status"] == "success"
    assert result["task_type"].startswith("explain")
    assert result["output"] and len(result["output"]) > 20


@needs_ollama
def test_router_embedding_fallback_on_dead_zone_prompts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Formulations HORS corpus d'entraînement et hors signaux mots-clés :
    la couche embeddings (bge-m3 réel) doit router par le sens. Tolérance
    1 erreur sur 4 (classifieur à 89 % en CV — pas un oracle)."""
    monkeypatch.setenv("AAC_ROUTER_EMBEDDINGS", "1")
    from app.task_classifier import classify_task

    cases = [
        ("il me faudrait une jolie représentation visuelle d'un port breton", "image_generation"),
        ("un décor 3d sobre pour photographier virtuellement ma lampe", "blender_script"),
        ("sois sans pitié avec mon paragraphe de conclusion", "critique"),
        ("que s'est-il passé cette semaine dans le monde de l'ia", "web_research"),
    ]
    correct = 0
    for text, expected in cases:
        task, reason = classify_task(text)
        if "embedding_fallback" not in reason:
            pytest.skip(f"couche embeddings inactive (modèle absent ?) : {reason[:80]}")
        correct += task == expected
    assert correct >= 3, f"{correct}/4 seulement"


# ---------------------------------------------------------------------------
# ComfyUI — génération réelle + manifest v2 + rejeu
# ---------------------------------------------------------------------------

@needs_comfyui
def test_comfyui_generate_capture_and_reproduce() -> None:
    from app.engine.executor import execute_request

    result = execute_request(
        "génère une image d'une tasse en céramique sur fond studio",
        mode="image_generation",
    )
    assert result["execution_summary"]["status"] == "success"
    assert result["artifact_path"] and Path(result["artifact_path"]).exists()

    # Capture : manifest v2 + bloc repro complet + sidecar rejouable.
    run_dir = Path(result["artifact_path"]).parent
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["manifest_version"] == 2
    assert manifest["repro"]["repro_version"] == repro.REPRO_VERSION
    (variant,) = manifest["repro"]["variants"]
    assert isinstance(variant["seed"], int)
    assert variant["image"]["pixels_sha256"]
    sidecar_path = run_dir / variant["workflow_file"]
    workflow = json.loads(sidecar_path.read_text(encoding="utf-8"))
    assert repro.sha256_canonical_json(workflow) == variant["workflow_sha256"]

    # Rejeu : vraie ré-exécution GPU. Le bit-exact est opportuniste (cuDNN),
    # le contrat produit est exact OU perceptual — jamais different.
    report = reproduce_comfyui(manifest, {1: workflow})
    assert report["verdict"] in (VERDICT_EXACT, VERDICT_PERCEPTUAL), report
    (replayed,) = report["variants"]
    assert replayed["image"]["dhash_distance"] <= 4


# ---------------------------------------------------------------------------
# Blender — génération réelle + manifest v2 + rejeu exact
# ---------------------------------------------------------------------------

@needs_ollama
@needs_blender
def test_blender_generate_capture_and_reproduce_exact() -> None:
    from app.engine.executor import execute_request

    result = execute_request(
        "un rendu produit d'une théière en cuivre sur fond studio neutre",
        mode="blender_script",
    )
    assert result["execution_summary"]["status"] == "success"
    assert result["blender_status"] == "success"

    run_dir = Path(result["blender_output_path"]).parent
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["manifest_version"] == 2
    block = manifest["repro"]
    assert block["repro_version"] == repro.REPRO_VERSION
    assert block["scene_py_sha256"]
    assert block["scene_report_semantic_sha256"]
    assert block["blender_version"]

    # Rejeu même-machine : Blender est déterministe (builder + EEVEE) — le
    # contrat est EXACT, pas seulement perceptual (mesuré live 2026-07-11).
    scene_py = (run_dir / "scene.py").read_text(encoding="utf-8")
    report = reproduce_blender(manifest, scene_py)
    assert report["verdict"] == VERDICT_EXACT, report
    semantic = next(c for c in report["checks"] if c["name"] == "scene_report_semantic")
    assert semantic["verdict"] == VERDICT_EXACT
