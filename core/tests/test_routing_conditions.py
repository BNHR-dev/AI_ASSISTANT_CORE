import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.engine.routing_conditions import enrich_route_config


def test_explain_with_code_triggers_build():
    base = {
        "task_type": "explain_basic",
        "primary_agent": "AGENT_PROF_IA",
        "selected_model": "qwen3:8b",
        "needs_web": False,
        "output_format": "explanation",
    }

    result = enrich_route_config(
        "explain_basic",
        "Explique-moi les embeddings et donne-moi un exemple de code en Python.",
        base,
    )

    assert result["second_call"] == "build"
    assert result["matched_rule"] == "explain_plus_code"


def test_critique_with_improvement_triggers_build():
    base = {
        "task_type": "critique",
        "primary_agent": "AGENT_EXAM_IA",
        "selected_model": "qwen3:8b",
        "needs_web": False,
        "output_format": "critique",
    }

    result = enrich_route_config(
        "critique",
        "Corrige ce code et propose une version améliorée.",
        base,
    )

    assert result["second_call"] == "build"
    assert result["matched_rule"] == "critique_plus_improvement"


def test_architecture_with_implementation_triggers_build():
    base = {
        "task_type": "architecture",
        "primary_agent": "AGENT_ARCHI_IA",
        "selected_model": "qwen3:14b",
        "needs_web": False,
        "output_format": "analysis",
    }

    result = enrich_route_config(
        "architecture",
        "Compare deux architectures de mémoire et propose une implémentation simple.",
        base,
    )

    assert result["second_call"] == "build"
    assert result["matched_rule"] == "architecture_plus_implementation"


def test_explain_without_code_request_stays_simple():
    base = {
        "task_type": "explain_basic",
        "primary_agent": "AGENT_PROF_IA",
        "selected_model": "qwen3:8b",
        "needs_web": False,
        "output_format": "explanation",
    }

    result = enrich_route_config(
        "explain_basic",
        "Explique-moi ce qu'est un embedding.",
        base,
    )

    assert result["second_call"] is None
    assert result["matched_rule"] is None
    assert result["reason_debug"] is None


def test_critique_without_improvement_request_stays_simple():
    base = {
        "task_type": "critique",
        "primary_agent": "AGENT_EXAM_IA",
        "selected_model": "qwen3:8b",
        "needs_web": False,
        "output_format": "critique",
    }

    result = enrich_route_config(
        "critique",
        "Analyse ce code et liste les erreurs.",
        base,
    )

    assert result["second_call"] is None
    assert result["matched_rule"] is None
    assert result["reason_debug"] is None


def test_architecture_without_implementation_request_stays_simple():
    base = {
        "task_type": "architecture",
        "primary_agent": "AGENT_ARCHI_IA",
        "selected_model": "qwen3:14b",
        "needs_web": False,
        "output_format": "analysis",
    }

    result = enrich_route_config(
        "architecture",
        "Compare deux architectures de mémoire.",
        base,
    )

    assert result["second_call"] is None
    assert result["matched_rule"] is None
    assert result["reason_debug"] is None


def test_base_config_is_not_mutated():
    base = {
        "task_type": "architecture",
        "primary_agent": "AGENT_ARCHI_IA",
        "selected_model": "qwen3:14b",
    }

    _ = enrich_route_config(
        "architecture",
        "Compare et propose une implémentation.",
        base,
    )

    assert "second_call" not in base
    assert "matched_rule" not in base
    assert "reason_debug" not in base
