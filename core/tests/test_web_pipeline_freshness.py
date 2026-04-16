from app.clients.web_client import WebSearchClientError, prepare_search_query, search_web
from app.engine.executor import execute_request
from app.engine.prompt_builder import build_web_synthesis_prompt


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_prepare_search_query_rewrites_french_latest_news_ia_query():
    query_info = prepare_search_query("cherche moi les dernières news IA")

    assert query_info["latest_request"] is True
    assert query_info["language"] == "fr"
    assert query_info["query_strategy"] == "latest_news_rewrite"
    assert query_info["broad_latest_query"] is True
    assert '"intelligence artificielle"' in query_info["query_used"]
    assert "actualités" in query_info["query_used"]
    assert "article" in query_info["query_used"]
    assert "-Iowa" in query_info["query_used"]


def test_search_web_uses_rewritten_latest_query(monkeypatch):
    captured_calls = []

    def fake_get(url, params=None, timeout=None):
        captured_calls.append({"url": url, "params": params or {}})
        return _FakeResponse({"results": []})

    monkeypatch.setattr("app.clients.web_client.requests.get", fake_get)

    search_web("cherche moi les dernières news IA")

    assert captured_calls
    assert all(call["params"]["time_range"] == "week" for call in captured_calls)
    assert any('"intelligence artificielle"' in call["params"]["q"] for call in captured_calls)
    assert any("actualités" in call["params"]["q"] for call in captured_calls)
    assert any("article" in call["params"]["q"] for call in captured_calls)
    assert any("-Iowa" in call["params"]["q"] for call in captured_calls)


def test_search_web_normalizes_and_prioritizes_article_results(monkeypatch):
    payload = {
        "results": [
            {
                "title": "Actualité IA",
                "url": "https://www.lemonde.fr/intelligence-artificielle/",
                "content": "Rubrique générale sur l'IA",
            },
            {
                "title": "Nouveau modèle publié 2026-04-05",
                "url": "https://example.com/ia/nouveau-modele-2026-04-05.html",
                "content": "Article daté 2026-04-05 sur un nouveau modèle.",
                "publishedDate": "2026-04-05",
            },
        ]
    }

    monkeypatch.setattr("app.clients.web_client.requests.get", lambda *args, **kwargs: _FakeResponse(payload))

    results = search_web("cherche moi les dernières news IA")

    assert results[0]["kind"] == "article"
    assert results[0]["source"] == "example.com"
    assert results[0]["published_at"] == "2026-04-05"
    assert results[0]["news_like"] is True
    assert results[1]["kind"] == "generic"


def test_search_web_marks_evergreen_listicle_as_not_news_like(monkeypatch):
    payload = {
        "results": [
            {
                "title": "Les 30 meilleurs blogs sur l'IA : pour s'informer sans se noyer dans ...",
                "url": "https://www.sitew.com/intelligence-artificielle/meilleurs-blogs-IA",
                "content": "Sélection de blogs IA pour apprendre et s'informer.",
                "publishedDate": "2026-04-03",
            }
        ]
    }

    monkeypatch.setattr("app.clients.web_client.requests.get", lambda *args, **kwargs: _FakeResponse(payload))

    results = search_web("cherche moi les dernières news IA")

    assert results[0]["kind"] == "article"
    assert results[0]["news_like"] is False


def test_search_web_raises_explicit_error_on_http_failure(monkeypatch):
    def fake_get(*args, **kwargs):
        import requests
        raise requests.RequestException("network down")

    monkeypatch.setattr("app.clients.web_client.requests.get", fake_get)

    try:
        search_web("news IA")
    except WebSearchClientError as exc:
        assert "network down" in str(exc)
    else:
        raise AssertionError("WebSearchClientError attendu")


def test_latest_news_pipeline_refuses_generic_or_stale_sources(monkeypatch):
    monkeypatch.setattr(
        "app.engine.step_executor.search_web",
        lambda query: [
            {
                "title": "Actu IA",
                "url": "https://www.lemonde.fr/intelligence-artificielle/",
                "content": "Rubrique générale IA",
                "source": "lemonde.fr",
                "published_at": None,
                "kind": "generic",
                "news_like": False,
            },
            {
                "title": "Sora et IA",
                "url": "https://www.lesnumeriques.com/intelligence-artificielle.html",
                "content": "Page catégorie IA",
                "source": "lesnumeriques.com",
                "published_at": "2025-09-01",
                "kind": "generic",
                "news_like": False,
            },
        ],
    )

    def fake_generate(model, prompt):
        raise AssertionError("Le LLM ne doit pas synthétiser des sources latest insuffisantes")

    monkeypatch.setattr("app.engine.step_executor.generate_with_ollama", fake_generate)

    result = execute_request("cherche moi les dernières news IA")

    assert result["task_type"] == "web_research"
    assert result["output"].startswith("Je n'ai pas trouvé assez d'articles d'actualité récents")
    assert "lemonde.fr" in result["output"]
    assert "lesnumeriques.com" in result["output"]


def test_web_synthesis_prompt_adds_freshness_guardrails_for_latest_news():
    prompt = build_web_synthesis_prompt(
        agent="AGENT_PROF_IA",
        output_format="synthèse + sources utiles + résumé clair",
        user_question="cherche moi les dernières news IA",
        latest_request=True,
        search_meta={
            "results_count": 3,
            "article_like_count": 1,
            "generic_results_count": 2,
            "dated_results_count": 1,
            "recent_dated_results_count": 0,
            "news_like_count": 1,
            "recent_news_like_count": 0,
            "selected_results_count": 0,
            "scope": "insufficient",
        },
        results=[
            {
                "title": "Exemple",
                "url": "https://example.com/article",
                "content": "contenu",
                "source": "example.com",
                "published_at": "2025-09-01",
                "kind": "article",
                "news_like": True,
            }
        ],
    )

    assert "Date du jour" in prompt
    assert "N'utilise pas une page générique" in prompt
    assert "N'inclus pas de rubrique “sources utiles” générique" in prompt
    assert "Source: example.com" in prompt
    assert "Date: 2025-09-01" in prompt
    assert "News-like: oui" in prompt


def test_search_web_marks_single_generic_topic_page_as_generic(monkeypatch):
    payload = {
        "results": [
            {
                "title": "Intelligence artificielle - Les Numériques",
                "url": "https://www.lesnumeriques.com/intelligence-artificielle.html",
                "content": "Rubrique générale sur l'IA",
            },
            {
                "title": "AWS Summit Paris 2026-04-01 : annonces IA",
                "url": "https://example.com/actualites/aws-summit-paris-2026-04-01-annonces-ia.html",
                "content": "Article daté 2026-04-01 sur les annonces IA.",
                "publishedDate": "2026-04-01",
            },
        ]
    }

    monkeypatch.setattr("app.clients.web_client.requests.get", lambda *args, **kwargs: _FakeResponse(payload))

    results = search_web("cherche moi les dernières news IA")

    assert results[0]["kind"] == "article"
    assert results[1]["kind"] == "generic"


def test_latest_news_pipeline_keeps_only_recent_news_articles_for_prompt(monkeypatch):
    captured = {}

    monkeypatch.setattr(
        "app.engine.step_executor.search_web",
        lambda query: [
            {
                "title": "AWS Summit Paris 2026-04-01 : annonces IA",
                "url": "https://example.com/actualites/aws-summit-paris-2026-04-01-annonces-ia.html",
                "content": "Article daté 2026-04-01 sur les annonces IA.",
                "source": "example.com",
                "published_at": "2026-04-01",
                "kind": "article",
                "news_like": True,
            },
            {
                "title": "Meta améliore le raisonnement LLM 2026-04-03",
                "url": "https://example.org/ia/meta-ameliore-le-raisonnement-llm-2026-04-03.html",
                "content": "Article daté 2026-04-03 sur Meta.",
                "source": "example.org",
                "published_at": "2026-04-03",
                "kind": "article",
                "news_like": True,
            },
            {
                "title": "Les 30 meilleurs blogs sur l'IA",
                "url": "https://www.sitew.com/intelligence-artificielle/meilleurs-blogs-IA",
                "content": "Guide evergreen",
                "source": "sitew.com",
                "published_at": "2026-04-03",
                "kind": "article",
                "news_like": False,
            },
            {
                "title": "Intelligence artificielle - Les Numériques",
                "url": "https://www.lesnumeriques.com/intelligence-artificielle.html",
                "content": "Rubrique générale sur l'IA",
                "source": "lesnumeriques.com",
                "published_at": None,
                "kind": "generic",
                "news_like": False,
            },
        ],
    )

    def fake_generate(model, prompt):
        captured["prompt"] = prompt
        return "SYNTHÈSE WEB PROPRE"

    monkeypatch.setattr("app.engine.step_executor.generate_with_ollama", fake_generate)

    result = execute_request("cherche moi les dernières news IA")

    search_meta = result["step_results"][0]["meta"]
    assert result["output"] == "SYNTHÈSE WEB PROPRE"
    assert search_meta["selected_results_count"] == 2
    assert search_meta["scope"] == "normal"
    assert search_meta["recent_news_like_count"] == 2
    assert len(search_meta["kept_results"]) == 2
    assert all(item["source"] != "sitew.com" for item in search_meta["kept_results"])
    assert all(item["source"] != "lesnumeriques.com" for item in search_meta["kept_results"])
    assert "sitew.com" not in captured["prompt"]
    assert "lesnumeriques.com" not in captured["prompt"]
    assert "example.com" in captured["prompt"]
    assert "example.org" in captured["prompt"]


def test_latest_news_pipeline_refuses_single_recent_news_article(monkeypatch):
    monkeypatch.setattr(
        "app.engine.step_executor.search_web",
        lambda query: [
            {
                "title": "AWS Summit Paris 2026-04-01 : annonces IA",
                "url": "https://example.com/actualites/aws-summit-paris-2026-04-01-annonces-ia.html",
                "content": "Article daté 2026-04-01 sur les annonces IA.",
                "source": "example.com",
                "published_at": "2026-04-01",
                "kind": "article",
                "news_like": True,
            },
            {
                "title": "Les 30 meilleurs blogs sur l'IA",
                "url": "https://www.sitew.com/intelligence-artificielle/meilleurs-blogs-IA",
                "content": "Guide evergreen",
                "source": "sitew.com",
                "published_at": "2026-04-03",
                "kind": "article",
                "news_like": False,
            },
        ],
    )

    def fake_generate(model, prompt):
        raise AssertionError("Le LLM ne doit pas synthétiser avec une seule vraie news récente")

    monkeypatch.setattr("app.engine.step_executor.generate_with_ollama", fake_generate)

    result = execute_request("cherche moi les dernières news IA")

    search_meta = result["step_results"][0]["meta"]
    assert result["output"].startswith("Je n'ai pas trouvé assez d'articles d'actualité récents")
    assert search_meta["selected_results_count"] == 0
    assert search_meta["scope"] == "insufficient"
    assert search_meta["recent_news_like_count"] == 1
    assert search_meta["required_recent_news_count"] == 2


def test_search_web_marks_sujet_page_as_generic_and_not_news_like(monkeypatch):
    payload = {
        "results": [
            {
                "title": "Toute l'actualité Intelligence artificielle - RTL",
                "url": "https://www.rtl.fr/sujet/intelligence-artificielle",
                "content": "Page sujet RTL sur l'intelligence artificielle.",
                "publishedDate": "2026-03-30",
            }
        ]
    }

    monkeypatch.setattr("app.clients.web_client.requests.get", lambda *args, **kwargs: _FakeResponse(payload))

    results = search_web("cherche moi les dernières news IA")

    assert results[0]["kind"] == "generic"
    assert results[0]["news_like"] is False


def test_search_web_marks_tribune_page_as_not_news_like(monkeypatch):
    payload = {
        "results": [
            {
                "title": "L'intelligence artificielle, entre révolution technologique et défis ...",
                "url": "https://itsocial.fr/intelligence-artificielle/intelligence-artificielle-tribunes/lintelligence-artificielle-entre-revolution-technologique-et-defis-juridiques-comment-concilier-innovation-et-securite/",
                "content": "Tribune sur l'intelligence artificielle et les enjeux juridiques.",
                "publishedDate": "2026-04-03",
            }
        ]
    }

    monkeypatch.setattr("app.clients.web_client.requests.get", lambda *args, **kwargs: _FakeResponse(payload))

    results = search_web("cherche moi les dernières news IA")

    assert results[0]["kind"] == "article"
    assert results[0]["news_like"] is False


def test_prepare_search_query_does_not_force_ai_topic_for_non_ai_latest_query():
    query_info = prepare_search_query("latest OpenAI news")

    assert query_info["latest_request"] is True
    assert query_info["language"] == "en"
    assert '"artificial intelligence"' not in query_info["query_used"]
    assert "openai" in query_info["query_used"].lower()


def test_search_web_latest_broad_query_uses_fallback_variants(monkeypatch):
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append((url, dict(params or {})))
        q = (params or {}).get("q", "")
        if "announcement" in q or "annonce" in q:
            return _FakeResponse(
                {
                    "results": [
                        {
                            "title": "OpenAI announces new API 2026-04-10",
                            "url": "https://example.com/openai-api-2026-04-10",
                            "content": "Fresh announcement.",
                            "publishedDate": "2026-04-10",
                        }
                    ]
                }
            )
        return _FakeResponse({"results": []})

    monkeypatch.setattr("app.clients.web_client.requests.get", fake_get)

    results = search_web("latest OpenAI news")

    assert len(calls) >= 2
    assert all(call[1]["time_range"] == "week" for call in calls)
    assert results[0]["source"] == "example.com"
    assert results[0]["news_like"] is True


def test_latest_news_pipeline_allows_30d_fallback_scope(monkeypatch):
    captured = {}

    monkeypatch.setattr(
        "app.engine.step_executor.search_web",
        lambda query: [
            {
                "title": "OpenAI annonce une mise à jour 2026-03-22",
                "url": "https://example.com/openai-update-2026-03-22",
                "content": "Article de news encore pertinent.",
                "source": "example.com",
                "published_at": "2026-03-22",
                "kind": "article",
                "news_like": True,
            },
            {
                "title": "Anthropic publie une annonce 2026-03-24",
                "url": "https://example.org/anthropic-update-2026-03-24",
                "content": "Deuxième article de news récent.",
                "source": "example.org",
                "published_at": "2026-03-24",
                "kind": "article",
                "news_like": True,
            },
        ],
    )

    def fake_generate(model, prompt):
        captured["prompt"] = prompt
        return "SYNTHÈSE WEB FALLBACK"

    monkeypatch.setattr("app.engine.step_executor.generate_with_ollama", fake_generate)

    result = execute_request("cherche moi les dernières news IA")

    search_meta = result["step_results"][0]["meta"]
    assert result["output"] == "SYNTHÈSE WEB FALLBACK"
    assert search_meta["scope"] == "broad_fallback_30d"
    assert search_meta["selected_results_count"] == 2
    assert "fallback élargi" in captured["prompt"].lower()
