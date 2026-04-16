from app.engine.visual_workflow_selector import analyze_visual_intent, select_visual_workflow


def test_visual_intent_analysis_portrait_poster():
    analysis = analyze_visual_intent("portrait pour une affiche de film")

    assert analysis.subject_type == "portrait"
    assert analysis.render_intent == "poster"
    assert analysis.workflow_id == "portrait_basic_v1"


def test_visual_intent_analysis_product_packshot():
    analysis = analyze_visual_intent("packshot de parfum luxe")

    assert analysis.subject_type == "product"
    assert analysis.render_intent == "packshot"
    assert analysis.workflow_id == "object_basic_v1"
    assert "luxury" in analysis.style_flags


def test_visual_intent_analysis_scene_cover():
    analysis = analyze_visual_intent("cover art d'une rue cyberpunk sous la pluie")

    assert analysis.subject_type == "scene"
    assert analysis.render_intent == "cover"
    assert analysis.workflow_id == "cinematic_scene_v1"
    assert "cyberpunk" in analysis.style_flags
    assert "rainy" in analysis.style_flags


def test_select_visual_workflow_keeps_legacy_tuple_contract():
    workflow_id, reason = select_visual_workflow("portrait cinématique d'un personnage sombre")

    assert workflow_id == "portrait_basic_v1"
    assert "workflow=portrait_basic_v1" in reason