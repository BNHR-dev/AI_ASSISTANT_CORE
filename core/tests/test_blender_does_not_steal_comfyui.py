"""
Tests : le pipeline Blender ne vole pas les requêtes ComfyUI / image_generation.
Vérifie aussi que "crée une scène Blender" va bien vers blender_script.
"""
import pytest

from app.engine.plan_builder import build_plan_from_decision
from app.engine.router_service import build_route_decision
from app.task_classifier import classify_task


# ---------------------------------------------------------------------------
# Classifier — les requêtes image_generation restent image_generation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("message", [
    "génère une image cyberpunk",
    "crée un rendu produit avec ComfyUI",
    "fais moi une image de paysage",
    "je veux une image de chat futuriste",
    "4 variantes d'un portrait",
    "concept art d'un dragon",
])
def test_image_generation_not_rerouted_to_blender(message):
    task_type, _ = classify_task(message)
    assert task_type == "image_generation", (
        f"'{message}' should stay image_generation, got {task_type}"
    )


# ---------------------------------------------------------------------------
# Router — image_generation donne visual_pipeline, pas blender_pipeline
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("message", [
    "génère une image cyberpunk",
    "crée un rendu produit avec ComfyUI",
])
def test_image_generation_routes_to_visual_pipeline(message):
    decision = build_route_decision(message)
    assert decision["selected_tool"] == "comfyui", (
        f"'{message}' should use comfyui tool, got {decision['selected_tool']}"
    )


def test_image_generation_plan_is_visual_pipeline():
    decision = build_route_decision("génère une image cyberpunk")
    plan = build_plan_from_decision(decision, "génère une image cyberpunk")
    assert plan.strategy == "visual_pipeline"
    assert plan.strategy != "blender_pipeline"


# ---------------------------------------------------------------------------
# Blender — les requêtes Blender ne déclenchent PAS visual_pipeline
# ---------------------------------------------------------------------------

def test_blender_scene_routes_to_blender_script():
    task_type, _ = classify_task("crée une scène Blender avec un cube métallique")
    assert task_type == "blender_script"


def test_blender_request_uses_blender_tool():
    decision = build_route_decision("crée une scène Blender avec un cube métallique")
    assert decision["selected_tool"] == "blender"
    assert decision["selected_tool"] != "comfyui"


def test_blender_request_plan_is_blender_pipeline():
    decision = build_route_decision("crée une scène Blender avec un cube métallique")
    plan = build_plan_from_decision(decision, "crée une scène Blender avec un cube métallique")
    assert plan.strategy == "blender_pipeline"
    assert plan.strategy != "visual_pipeline"


# ---------------------------------------------------------------------------
# Séparation stricte : aucune requête ne produit les deux pipelines
# ---------------------------------------------------------------------------

def test_comfyui_message_never_produces_blender_steps():
    decision = build_route_decision("génère une image cyberpunk avec ComfyUI")
    plan = build_plan_from_decision(decision, "génère une image cyberpunk avec ComfyUI")
    step_types = [s.step_type for s in plan.steps]
    assert "prepare_blender_script" not in step_types
    assert "tool_blender" not in step_types


def test_blender_message_never_produces_comfyui_steps():
    decision = build_route_decision("crée une scène Blender avec un cube")
    plan = build_plan_from_decision(decision, "crée une scène Blender avec un cube")
    step_types = [s.step_type for s in plan.steps]
    assert "prepare_visual" not in step_types
    assert "tool_comfyui" not in step_types
