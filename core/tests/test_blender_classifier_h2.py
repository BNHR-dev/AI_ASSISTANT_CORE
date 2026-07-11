from app.task_classifier import classify_task


def test_classifier_routes_blender_keyword_to_blender_script_fr():
    """Depuis l'ajout de blender_script, les requêtes Blender vont vers blender_script."""
    task, _ = classify_task("écris un script Blender pour créer un cube")
    assert task == "blender_script"


def test_classifier_routes_bpy_to_blender_script_fr():
    task, _ = classify_task("script bpy pour modéliser un objet")
    assert task == "blender_script"


def test_classifier_blender_scene_not_image_generation_fr():
    task, _ = classify_task("crée une scène Blender")
    assert task == "blender_script"
    assert task != "image_generation"


def test_classifier_blender_routes_to_blender_script_en():
    task, _ = classify_task("write a Blender script to create a cube")
    assert task == "blender_script"


def test_contains_build_intent_blender():
    """bpy/blender ne sont plus dans build_terms depuis l'ajout de blender_script."""
    # les mots blender/bpy forcent blender_script via guardrail
    task_blender, _ = classify_task("blender script")
    assert task_blender == "blender_script"
    task_bpy, _ = classify_task("bpy")
    assert task_bpy == "blender_script"


def test_classifier_explain_blender_not_build():
    task, _ = classify_task("explique ce qu'est Blender")
    assert task == "blender_script"  # 'blender' force blender_script via guardrail
    assert task != "build"
