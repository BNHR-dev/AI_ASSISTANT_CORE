from app.task_classifier import classify_task


def test_visual_request_fr():
    task, _ = classify_task("fais-moi un visuel cyberpunk")
    assert task == "image_generation"


def test_packshot_request_fr():
    task, _ = classify_task("crée un packshot de parfum luxe")
    assert task == "image_generation"


def test_build_over_visual_guardrail():
    task, _ = classify_task("écris un script python pour générer une image")
    assert task == "build"


def test_architecture_over_visual_guardrail_fr():
    task, _ = classify_task("architecture d'un pipeline image generation")
    assert task == "architecture"


def test_architecture_over_visual_guardrail_en():
    task, _ = classify_task("create a visual pipeline in FastAPI")
    assert task == "architecture"


def test_visual_request_en():
    task, _ = classify_task("make me an image of a cyberpunk alley")
    assert task == "image_generation"


def test_vision_when_has_image():
    task, _ = classify_task("analyse cette image", has_image=True)
    assert task == "vision"


def test_explain_basic_apostrophe_fr():
    task, _ = classify_task("c'est quoi les embeddings")
    assert task == "explain_basic"


def test_architecture_apostrophe_fr():
    task, _ = classify_task("j'hésite entre deux architectures")
    assert task == "architecture"


def test_architecture_apostrophe_en():
    task, _ = classify_task("i'm hesitating between two pipelines")
    assert task == "architecture"
