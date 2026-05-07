from app.engine.prompt_builder import (
    build_primary_prompt,
    build_second_call_prompt,
    build_web_synthesis_prompt,
)


def test_primary_prompt_includes_explain_contract_titles():
    prompt = build_primary_prompt(
        agent="AGENT_PROF_IA",
        output_format="définition + image mentale + exemple concret",
        message="explique les embeddings",
        task_type="explain_basic",
    )

    assert "Format attendu" in prompt
    assert "1. Définition" in prompt
    assert "2. Image mentale" in prompt
    assert "3. Exemple concret" in prompt


def test_second_call_prompt_includes_build_contract_titles():
    prompt = build_second_call_prompt(
        agent="AGENT_BUILDER_IA",
        output_format="module python + structure + instructions de test + usage",
        user_question="fais un parseur",
        primary_output="explication",
        requested_task_type="build",
    )

    assert "1. Objectif" in prompt
    assert "2. Code" in prompt
    assert "3. Tests rapides" in prompt
    assert "4. Usage" in prompt


def test_web_prompt_includes_sources_retained_contract():
    prompt = build_web_synthesis_prompt(
        agent="AGENT_PROF_IA",
        output_format="synthèse + sources utiles + résumé clair",
        user_question="cherche les dernières news IA",
        results=[
            {
                "title": "Titre",
                "url": "https://example.com/a",
                "content": "Contenu",
                "source": "example.com",
                "published_at": "2026-04-05",
                "kind": "article",
                "news_like": True,
            }
        ],
        latest_request=True,
        search_meta={"results_count": 1, "selected_results_count": 1},
    )

    assert "1. Synthèse" in prompt
    assert "2. Points clés" in prompt
    assert "3. Sources retenues" in prompt


def test_primary_build_prompt_adds_quality_guardrails():
    prompt = build_primary_prompt(
        agent="AGENT_BUILDER_IA",
        output_format="module python + structure + instructions de test + usage",
        message="écris un parseur csv minimal",
        task_type="build",
    )

    assert "sans TODO ni pseudo-code".lower() in prompt.lower()
    assert "bibliothèque standard".lower() in prompt.lower()


def test_primary_build_prompt_forbids_intro():
    prompt = build_primary_prompt(
        agent="AGENT_BUILDER_IA",
        output_format="module python + structure + instructions de test + usage",
        message="écris un parseur csv minimal",
        task_type="build",
    )

    assert "produis directement le livrable" in prompt.lower()


def test_second_call_prompt_reuses_handoff_constraints_for_build():
    prompt = build_second_call_prompt(
        agent="AGENT_BUILDER_IA",
        output_format="module python + structure + instructions de test + usage",
        user_question="explique puis code un parseur csv",
        primary_output="Utilise csv.DictReader et gère les lignes vides.",
        requested_task_type="build",
    )

    assert "réutilise explicitement".lower() in prompt.lower()
    assert "ne repars pas de zéro".lower() in prompt.lower()


def test_second_call_build_prompt_forbids_intro():
    prompt = build_second_call_prompt(
        agent="AGENT_BUILDER_IA",
        output_format="module python + structure + instructions de test + usage",
        user_question="explique puis code un parseur csv",
        primary_output="Utilise csv.DictReader et gère les lignes vides.",
        requested_task_type="build",
    )

    assert "produis directement le livrable" in prompt.lower()


def test_vision_primary_prompt_includes_contract_sections():
    prompt = build_primary_prompt(
        agent="AGENT_VISION_IA",
        output_format="description + analysis + visual_interpretation",
        message="que vois-tu dans cette image ?",
        task_type="vision",
    )

    assert "1. Description" in prompt
    assert "2. Analyse" in prompt
    assert "3. Interprétation prudente" in prompt


def test_vision_primary_prompt_anchors_on_image():
    prompt = build_primary_prompt(
        agent="AGENT_VISION_IA",
        output_format="description + analysis + visual_interpretation",
        message="que vois-tu dans cette image ?",
        task_type="vision",
    )

    assert "image fournie" in prompt.lower() or "visible dans l'image" in prompt.lower()


def test_web_synthesis_prompt_forbids_meta_reproduction():
    prompt = build_web_synthesis_prompt(
        agent="AGENT_PROF_IA",
        output_format="synthèse + sources utiles + résumé clair",
        user_question="cherche les dernières news IA",
        results=[
            {
                "title": "Titre",
                "url": "https://example.com/a",
                "content": "Contenu",
                "source": "example.com",
                "published_at": "2026-04-05",
                "kind": "article",
                "news_like": True,
            }
        ],
    )

    assert "résumé technique" in prompt.lower()
    assert "contexte interne" in prompt.lower()
