import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.engine.router_service import build_route_decision


def test_route_decision_adds_second_call_for_explain_plus_code():
    result = build_route_decision(
        "Explique-moi les embeddings et donne-moi un exemple de code en Python.",
        False,
    )

    assert result["task_type"] == "explain_basic"
    assert result["second_call"] == "build"
    assert result["matched_rule"] == "explain_plus_code"


def test_route_decision_adds_second_call_for_architecture_plus_implementation():
    result = build_route_decision(
        "Compare deux architectures de mémoire et propose une implémentation simple.",
        False,
    )

    assert result["task_type"] == "architecture"
    assert result["second_call"] == "build"


# Note : les deux anciens tests test_execute_request_* ont été supprimés.
# Ils monkeypatchaient une surface obsolète (app.engine.executor.generate_with_ollama
# et .search_web, qui ont migré dans step_executor) et vérifiaient un schéma de retour
# qui n'existe plus (primary_output / second_output / output au lieu de step_results[]).
# La couverture fonctionnelle équivalente est maintenue par :
#   - tests/test_multistep_executor.py::test_execute_request_multistep
#   - tests/test_baseline_v17_regression.py::test_explain_plus_code_runs_two_step_llm
#   - tests/test_baseline_v17_regression.py::test_web_pipeline_hides_technical_output
