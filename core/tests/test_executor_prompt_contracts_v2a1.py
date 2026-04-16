from app.engine.executor import execute_request



def test_execute_request_passes_explain_contract_to_primary_prompt(monkeypatch):
    captured = {}

    def fake_generate(model: str, prompt: str) -> str:
        captured["model"] = model
        captured["prompt"] = prompt
        return "OK"

    monkeypatch.setattr("app.engine.step_executor.generate_with_ollama", fake_generate)

    result = execute_request("explique moi les embeddings")

    assert result["task_type"] == "explain_basic"
    assert "1. Définition" in captured["prompt"]
    assert "3. Exemple concret" in captured["prompt"]



def test_execute_request_passes_build_contract_to_secondary_prompt(monkeypatch):
    prompts = []

    def fake_generate(model: str, prompt: str) -> str:
        prompts.append(prompt)
        return "OUT"

    monkeypatch.setattr("app.engine.step_executor.generate_with_ollama", fake_generate)

    result = execute_request("explique moi les embeddings avec un exemple python")

    assert result["second_call"] == "build"
    assert len(prompts) == 2
    assert "1. Objectif" in prompts[1]
    assert "2. Code" in prompts[1]
    assert "4. Usage" in prompts[1]



def test_execute_request_passes_web_contract_to_synthesis_prompt(monkeypatch):
    monkeypatch.setattr(
        "app.engine.step_executor.search_web",
        lambda query: [
            {
                "title": "Annonce produit 2026-04-05",
                "url": "https://example.com/news/annonce-produit-2026-04-05.html",
                "content": "Article de news récent.",
                "source": "example.com",
                "published_at": "2026-04-05",
                "kind": "article",
                "news_like": True,
            },
            {
                "title": "Meta publie une mise à jour 2026-04-04",
                "url": "https://example.org/news/meta-publie-2026-04-04.html",
                "content": "Autre article de news récent.",
                "source": "example.org",
                "published_at": "2026-04-04",
                "kind": "article",
                "news_like": True,
            },
        ],
    )
    captured = {}

    def fake_generate(model: str, prompt: str) -> str:
        captured["prompt"] = prompt
        return "SYNTHÈSE WEB"

    monkeypatch.setattr("app.engine.step_executor.generate_with_ollama", fake_generate)

    result = execute_request("cherche moi les dernières news IA")

    assert result["task_type"] == "web_research"
    assert "1. Synthèse" in captured["prompt"]
    assert "3. Sources retenues" in captured["prompt"]


def test_execute_request_secondary_build_prompt_includes_handoff_guardrails(monkeypatch):
    prompts = []

    def fake_generate(model: str, prompt: str) -> str:
        prompts.append(prompt)
        return "OUT"

    monkeypatch.setattr("app.engine.step_executor.generate_with_ollama", fake_generate)

    execute_request("explique moi les embeddings avec un exemple python")

    assert len(prompts) == 2
    assert "réutilise explicitement".lower() in prompts[1].lower()
    assert "sans TODO ni pseudo-code".lower() in prompts[1].lower()
