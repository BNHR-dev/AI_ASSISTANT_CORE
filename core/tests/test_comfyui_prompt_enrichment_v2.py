from app.clients.comfyui_client import build_visual_request, enrich_visual_positive_prompt
from app.engine.visual_workflow_selector import analyze_visual_intent


def test_enrich_visual_positive_prompt_portrait_poster():
    analysis = analyze_visual_intent("portrait pour une affiche de film cyberpunk")
    prompt = enrich_visual_positive_prompt(
        user_prompt="portrait pour une affiche de film cyberpunk",
        workflow_id=analysis.workflow_id,
        analysis=analysis,
    )

    assert "poster composition" in prompt
    assert "hero framing" in prompt
    assert "cyberpunk aesthetic" in prompt
    assert "portrait composition" in prompt


def test_enrich_visual_positive_prompt_product_packshot():
    analysis = analyze_visual_intent("packshot de parfum luxe")
    prompt = enrich_visual_positive_prompt(
        user_prompt="packshot de parfum luxe",
        workflow_id=analysis.workflow_id,
        analysis=analysis,
    )

    assert "studio packshot" in prompt
    assert "product photography" in prompt
    assert "clean product composition" in prompt
    assert "luxury feel" in prompt


def test_build_visual_request_applies_poster_format():
    analysis = analyze_visual_intent("affiche sci-fi")
    request = build_visual_request(
        prompt="affiche sci-fi",
        workflow_id=analysis.workflow_id,
        analysis=analysis,
    )

    assert request.width == 832
    assert request.height == 1216


def test_build_visual_request_applies_scene_landscape_format():
    analysis = analyze_visual_intent("une rue cyberpunk sous la pluie")
    request = build_visual_request(
        prompt="une rue cyberpunk sous la pluie",
        workflow_id=analysis.workflow_id,
        analysis=analysis,
    )

    assert request.width == 1216
    assert request.height == 832


def test_build_visual_request_applies_packshot_square_format():
    analysis = analyze_visual_intent("packshot de sneaker premium")
    request = build_visual_request(
        prompt="packshot de sneaker premium",
        workflow_id=analysis.workflow_id,
        analysis=analysis,
    )

    assert request.width == 1024
    assert request.height == 1024