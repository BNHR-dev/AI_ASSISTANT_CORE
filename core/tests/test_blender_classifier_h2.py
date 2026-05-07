from app.task_classifier import classify_task, contains_build_intent, normalize_text


def test_classifier_routes_blender_keyword_to_build_fr():
    task, _ = classify_task("écris un script Blender pour créer un cube")
    assert task == "build"


def test_classifier_routes_bpy_to_build_fr():
    task, _ = classify_task("script bpy pour modéliser un objet")
    assert task == "build"


def test_classifier_blender_scene_not_image_generation_fr():
    task, _ = classify_task("crée une scène Blender")
    assert task == "build"
    assert task != "image_generation"


def test_classifier_blender_routes_to_build_en():
    task, _ = classify_task("write a Blender script to create a cube")
    assert task == "build"


def test_contains_build_intent_blender():
    assert contains_build_intent(normalize_text("blender script"))
    assert contains_build_intent(normalize_text("bpy script"))
    assert contains_build_intent(normalize_text("script blender"))
    assert contains_build_intent(normalize_text("bpy"))


def test_classifier_explain_blender_not_build():
    task, _ = classify_task("explique ce qu'est Blender")
    assert task == "explain_basic"
    assert task != "build"
