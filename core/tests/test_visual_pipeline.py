from __future__ import annotations

from app.engine.visual_types import VisualRequest
from app.engine.visual_workflow_selector import select_visual_workflow


def test_visual_workflow_selector_scene_detected_with_standard_render_default():
    workflow_id, reason = select_visual_workflow("génère une image cyberpunk avec néon humide")
    assert workflow_id == "cinematic_scene_v1"
    assert "subject=scene" in reason
    assert "render=standard" in reason
    assert "render_default=standard" in reason


def test_visual_workflow_selector_portrait():
    workflow_id, reason = select_visual_workflow("portrait cinématique d'un personnage sombre")
    assert workflow_id == "portrait_basic_v1"


def test_visual_request_defaults():
    request = VisualRequest(
        workflow_id="cinematic_scene_v1",
        positive_prompt="cyberpunk alley",
    )

    assert request.negative_prompt
    assert request.seed == 42
    assert request.width == 1024
    assert request.height == 1024
