from datetime import date, timedelta

from app.engine.executor import execute_request


def _recent(days_ago: int) -> str:
    return str(date.today() - timedelta(days=days_ago))


def test_explain_plus_code_runs_two_step_llm(monkeypatch):
    def fake_generate(model: str, prompt: str) -> str:
        if "Réponse du premier appel" in prompt:
            return "CODE_BLOCK"
        return "EXPLAIN_BLOCK"

    monkeypatch.setattr("app.engine.step_executor.generate_with_ollama", fake_generate)

    result = execute_request("explique moi les embeddings avec un exemple python")

    assert result["task_type"] == "explain_basic"
    assert result["second_call"] == "build"
    assert result["primary_output"] == "EXPLAIN_BLOCK"
    assert result["second_output"] == "CODE_BLOCK"
    assert result["plan"][0]["step_type"] == "llm_primary"
    assert result["plan"][1]["step_type"] == "llm_secondary"
    assert "EXPLAIN_BLOCK" in result["output"]
    assert "CODE_BLOCK" in result["output"]


def test_web_pipeline_hides_technical_output(monkeypatch):
    monkeypatch.setattr(
        "app.engine.step_executor.search_web",
        lambda query: [
            {
                "title": "Annonce produit récente",
                "url": "https://example.com/news/annonce-produit-recent.html",
                "content": "Article de news récent.",
                "source": "example.com",
                "published_at": _recent(3),
                "kind": "article",
                "news_like": True,
            },
            {
                "title": "Meta publie une mise à jour récente",
                "url": "https://example.org/news/meta-publie-recent.html",
                "content": "Autre article de news récent.",
                "source": "example.org",
                "published_at": _recent(4),
                "kind": "article",
                "news_like": True,
            },
        ],
    )
    monkeypatch.setattr(
        "app.engine.step_executor.generate_with_ollama",
        lambda model, prompt: "SYNTHÈSE WEB",
    )

    result = execute_request("cherche moi les dernières news IA")

    assert result["task_type"] == "web_research"
    assert result["plan"][0]["step_type"] == "tool_web_search"
    assert result["plan"][1]["step_type"] == "llm_synthesis"
    assert result["output"] == "SYNTHÈSE WEB"
    assert "résultats web récupérés" not in result["output"].lower()



def test_image_generation_routes_to_comfyui(monkeypatch):
    monkeypatch.setattr(
        "app.engine.step_executor.run_comfyui_workflow",
        lambda request: {
            "prompt_id": "p1",
            "history": {},
            "output_path": "AI_ASSISTANT_CORE/fake.png",
        },
    )

    result = execute_request("génère une image cyberpunk")

    assert result["task_type"] == "image_generation"
    assert result["selected_tool"] == "comfyui"
    assert result["plan"][0]["step_type"] == "prepare_visual"
    assert result["plan"][1]["step_type"] == "tool_comfyui"
    assert "fake.png" in result["output"]



def test_build_request_stays_single_step(monkeypatch):
    monkeypatch.setattr(
        "app.engine.step_executor.generate_with_ollama",
        lambda model, prompt: "BUILD_OUTPUT",
    )

    result = execute_request("écris moi un script python simple")

    assert result["task_type"] == "build"
    assert result["second_call"] is None
    assert len(result["plan"]) == 1
    assert result["output"] == "BUILD_OUTPUT"
