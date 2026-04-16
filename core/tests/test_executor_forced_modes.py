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
