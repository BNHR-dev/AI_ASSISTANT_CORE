import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.engine.router_service import build_route_decision
from app.engine.executor import execute_request


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


def test_execute_request_runs_primary_and_second_call(monkeypatch):
    outputs = []

    def fake_generate(model: str, prompt: str) -> str:
        outputs.append((model, prompt))
        return f"OUTPUT[{model}]"

    monkeypatch.setattr("app.engine.executor.generate_with_ollama", fake_generate)

    result = execute_request(
        "Explique-moi les embeddings et donne-moi un exemple de code en Python.",
        False,
    )

    assert result["second_call"] == "build"
    assert result["primary_output"] == "OUTPUT[qwen3:8b]"
    assert result["second_output"] == "OUTPUT[qwen2.5-coder:14b]"
    assert "---" in result["output"]
    assert len(outputs) == 2


def test_execute_request_uses_needs_web_flag(monkeypatch):
    def fake_search_web(message: str):
        return [{"title": "T1", "url": "https://example.com", "content": "C1"}]

    def fake_generate(model: str, prompt: str) -> str:
        assert "Résultats web" in prompt
        return f"WEB_OUTPUT[{model}]"

    monkeypatch.setattr("app.engine.executor.search_web", fake_search_web)
    monkeypatch.setattr("app.engine.executor.generate_with_ollama", fake_generate)

    result = execute_request("Cherche les dernières avancées sur la fusion nucléaire", False)

    assert result["task_type"] == "web_research"
    assert result["needs_web"] is True
    assert result["primary_output"] == "WEB_OUTPUT[qwen3:14b]"
    assert result["second_output"] is None
