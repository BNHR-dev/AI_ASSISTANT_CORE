import pytest

from app.engine.executor import _build_forced_mode_decision


def test_forced_explain_mode_keeps_hybrid_rule_and_second_call():
    decision = _build_forced_mode_decision(
        "explique moi les embeddings avec un exemple python",
        "explain",
    )

    assert decision["task_type"] == "explain_basic"
    assert decision["second_call"] == "build"
    assert decision["matched_rule"] == "explain_plus_code"
    assert decision["classifier_reason"] == "Mode forcé: explain"
    assert decision["reason_debug"] == (
        "Mode forcé: explain | demande de code détectée (explicite ou implicite)"
    )
    assert decision["decision_path"][:3] == [
        "forced_mode → explain",
        "forced_task → explain_basic",
        "final_task → explain_basic",
    ]


def test_forced_architecture_mode_keeps_implementation_rule():
    decision = _build_forced_mode_decision(
        "compare deux architectures et propose une implémentation simple",
        "architecture",
    )

    assert decision["task_type"] == "architecture"
    assert decision["second_call"] == "build"
    assert decision["matched_rule"] == "architecture_plus_implementation"
    assert "rule → architecture_plus_implementation" in decision["decision_trace"]


def test_forced_build_mode_stays_single_step_without_extra_rule():
    decision = _build_forced_mode_decision(
        "écris moi un script python simple",
        "build",
    )

    assert decision["task_type"] == "build"
    assert decision["second_call"] is None
    assert decision["matched_rule"] is None
    assert decision["selected_tool"] is None


# GAP B — modes forcés manquants


def test_forced_vision_mode_task_type_and_classifier_reason():
    decision = _build_forced_mode_decision(
        "décris ce que tu vois dans cette image",
        "vision",
    )

    assert decision["task_type"] == "vision"
    assert decision["classifier_reason"] == "Mode forcé: vision"
    assert decision["decision_path"][:2] == [
        "forced_mode → vision",
        "forced_task → vision",
    ]


def test_forced_image_generation_mode_forces_comfyui_tool():
    decision = _build_forced_mode_decision(
        "génère une image cyberpunk",
        "image_generation",
    )

    assert decision["task_type"] == "image_generation"
    assert decision["selected_tool"] == "comfyui"
    assert decision["classifier_reason"] == "Mode forcé: image_generation"
    assert "forced_tool → comfyui" in decision["decision_trace"]


def test_forced_web_research_mode_enables_web():
    decision = _build_forced_mode_decision(
        "cherche les dernières news sur l'IA",
        "web_research",
    )

    assert decision["task_type"] == "web_research"
    assert decision["needs_web"] is True
    assert decision["classifier_reason"] == "Mode forcé: web_research"


def test_forced_critique_mode_task_type_and_classifier_reason():
    decision = _build_forced_mode_decision(
        "critique ce code python",
        "critique",
    )

    assert decision["task_type"] == "critique"
    assert decision["classifier_reason"] == "Mode forcé: critique"
    assert decision["decision_path"][:2] == [
        "forced_mode → critique",
        "forced_task → critique",
    ]


# GAP F — mode inconnu


def test_forced_unknown_mode_raises_value_error():
    with pytest.raises(ValueError):
        _build_forced_mode_decision("peu importe", "unknown_mode")
