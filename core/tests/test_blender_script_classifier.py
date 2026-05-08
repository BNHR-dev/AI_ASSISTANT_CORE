"""
Tests : le classifier route correctement vers blender_script sans voler
les requêtes image_generation / ComfyUI ni les requêtes build génériques.
"""
import pytest

from app.task_classifier import classify_task


@pytest.mark.parametrize("message", [
    "crée une scène Blender avec un cube métallique",
    "écris un script bpy pour créer un cube rouge",
    "modélise un personnage dans Blender",
    "génère une scène 3D dans Blender",
    "bpy script pour ajouter une lumière",
])
def test_blender_messages_route_to_blender_script(message):
    task_type, _ = classify_task(message)
    assert task_type == "blender_script", (
        f"Expected blender_script for '{message}', got {task_type}"
    )


@pytest.mark.parametrize("message", [
    "génère une image cyberpunk",
    "crée un rendu produit avec ComfyUI",
    "fais moi une image de paysage",
    "je veux une image de chat",
])
def test_image_generation_not_stolen(message):
    task_type, _ = classify_task(message)
    assert task_type == "image_generation", (
        f"Expected image_generation for '{message}', got {task_type}"
    )


def test_generic_python_script_not_blender():
    """'crée un script Python simple' ne doit pas aller vers blender_script."""
    task_type, _ = classify_task("crée un script Python simple pour lire un fichier CSV")
    assert task_type != "blender_script", (
        f"Generic Python request should not route to blender_script, got {task_type}"
    )


def test_blender_explicit_keyword_forces_blender_script():
    """Le mot 'blender' seul suffit à forcer blender_script."""
    task_type, _ = classify_task("je veux créer quelque chose dans Blender")
    assert task_type == "blender_script"


def test_bpy_explicit_keyword_forces_blender_script():
    """Le mot 'bpy' seul suffit à forcer blender_script."""
    task_type, _ = classify_task("comment utiliser bpy pour ajouter un mesh")
    assert task_type == "blender_script"
