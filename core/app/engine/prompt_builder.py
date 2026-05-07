from __future__ import annotations

from datetime import datetime, timezone

from app.engine.agent_prompt_registry import get_agent_system_prompt
from app.engine.output_contracts import render_output_contract


PROMPT_LABELS = {
    "fr": {
        "respond_in_language": "Tu dois répondre en français.",
        "respect_output_format": "Tu dois respecter ce format de sortie : {output_format}",
        "stage_constraints": "Contraintes d'étape :",
        "user_request": "Demande utilisateur :",
        "initial_request": "Demande initiale :",
        "first_call_response": "Réponse du premier appel :",
        "user_question": "Question utilisateur :",
        "web_results": "Résultats web :",
        "technical_summary": "Résumé technique des résultats :",
        "unknown_date": "inconnue",
        "yes": "oui",
        "no": "non",
        "primary_step_pipeline": "Tu es dans l'étape 1 d'un pipeline multi-step.",
        "no_final_code_yet": "Explique clairement, mais ne produis pas le code final détaillé.",
        "reserve_full_technical_deliverable": "Réserve le livrable technique complet à l'étape suivante.",
        "build_priority": "Priorité au livrable concret.",
        "build_deliverable": "Donne directement un résultat exploitable, avec structure, tests simples et usage.",
        "build_complete_code": "Le code doit être complet, cohérent, exécutable et sans TODO ni pseudo-code.",
        "build_state_assumptions": "Si une hypothèse est nécessaire, écris-la explicitement puis continue avec la version minimale raisonnable.",
        "build_small_dependencies": "Évite les dépendances inutiles ; préfère la bibliothèque standard sauf demande contraire.",
        "build_edge_cases": "Prévois un minimum de validation d'entrée, d'erreur simple ou de garde-fous utiles.",
        "build_no_intro": "Ne commence pas par une introduction, une reformulation de la demande ou un commentaire sur ta propre réponse. Produis directement le livrable.",
        "vision_anchor_on_image": "Appuie-toi uniquement sur ce qui est visible dans l'image fournie. Si tu n'as pas accès à l'image ou qu'elle est illisible, indique-le sans décrire un sujet générique.",
        "architecture_priority": "Priorité à la décision pragmatique.",
        "architecture_avoid_academic": "Évite le blabla académique et la sur-ingénierie.",
        "secondary_step_pipeline": "Tu es dans l'étape 2 d'un pipeline multi-step.",
        "avoid_repetition": "Ne répète pas inutilement l'explication précédente.",
        "build_direct_deliverable": "Produis directement le livrable technique.",
        "build_focus": "Privilégie le code, la structure, les tests simples et l'usage.",
        "build_reuse_handoff": "Réutilise explicitement les contraintes, choix utiles et pièges déjà apparus dans le premier appel.",
        "build_keep_decisions": "Ne repars pas de zéro si le premier appel a déjà cadré une approche valable.",
        "avoid_repeating_theory": "Évite de refaire toute la théorie.",
        "do_not_invent": "N'invente rien.",
        "only_given_sources": "N'ajoute pas de source non présente dans le contexte.",
        "cite_only_real_links": "Cite uniquement les liens réellement fournis.",
        "be_concise": "Va à l'essentiel.",
        "web_no_reproduce_meta": "Ne reproduis pas le résumé technique des résultats dans ta réponse ; il est fourni uniquement pour ton contexte interne.",
        "latest_recent_news_1": "La question porte sur des actualités récentes.",
        "latest_recent_news_2": "Date du jour : {today}.",
        "latest_recent_news_3": "N'utilise pas une page générique, une rubrique, une homepage ou un contenu evergreen comme preuve d'un fait d'actualité.",
        "latest_recent_news_4": "Les sources ci-dessous ont déjà été filtrées : ne travaille qu'à partir d'elles.",
        "latest_recent_news_5": "Si le périmètre est insuffisant, dis-le clairement au lieu d'inventer une synthèse.",
        "latest_recent_news_6": "N'inclus pas de rubrique “sources utiles” générique : cite seulement les articles réellement présents ci-dessous.",
        "latest_recent_news_7": "N'ajoute jamais un item avec mention “à vérifier”, “semble”, “pourrait” dans le résumé principal.",
        "latest_recent_news_8": "N'utilise pas comme actualités des pages sujet, des tags, des rubriques, des tribunes, des opinions, des dossiers, des comparatifs ou des guides.",
        "latest_recent_news_9": "Si le périmètre est un fallback élargi, signale brièvement que la couverture est plus large et potentiellement moins fraîche que la fenêtre stricte.",
        "meta_total_results": "résultats total",
        "meta_article_results": "résultats type article",
        "meta_generic_results": "résultats type page générique",
        "meta_dated_results": "résultats datés",
        "meta_recent_dated_results": "résultats datés récents",
        "meta_news_like_results": "résultats news-like",
        "meta_recent_news_like_results": "résultats news-like récents",
        "meta_selected_results": "résultats retenus pour synthèse",
        "meta_scope": "périmètre",
        "title": "Titre",
        "source": "Source",
        "date": "Date",
        "type": "Type",
        "news_like": "News-like",
        "url": "URL",
        "content": "Contenu",
    },
    "en": {
        "respond_in_language": "You must answer in English.",
        "respect_output_format": "You must follow this output format: {output_format}",
        "stage_constraints": "Stage constraints:",
        "user_request": "User request:",
        "initial_request": "Initial request:",
        "first_call_response": "First call response:",
        "user_question": "User question:",
        "web_results": "Web results:",
        "technical_summary": "Technical result summary:",
        "unknown_date": "unknown",
        "yes": "yes",
        "no": "no",
        "primary_step_pipeline": "You are in step 1 of a multi-step pipeline.",
        "no_final_code_yet": "Explain clearly, but do not produce the final detailed code.",
        "reserve_full_technical_deliverable": "Reserve the full technical deliverable for the next step.",
        "build_priority": "Prioritize a concrete deliverable.",
        "build_deliverable": "Provide a directly usable result, with structure, simple tests, and usage.",
        "build_complete_code": "The code must be complete, coherent, executable, and contain no TODOs or pseudocode.",
        "build_state_assumptions": "If an assumption is required, state it explicitly and continue with the smallest reasonable version.",
        "build_small_dependencies": "Avoid unnecessary dependencies; prefer the standard library unless the user asked otherwise.",
        "build_edge_cases": "Include minimal input validation, simple error handling, or useful guardrails.",
        "build_no_intro": "Do not start with an introduction, a restatement of the request, or a comment about your own response. Produce the deliverable directly.",
        "vision_anchor_on_image": "Base your answer solely on what is visible in the provided image. If you have no access to the image or it is unreadable, say so instead of describing a generic subject.",
        "architecture_priority": "Prioritize pragmatic decision-making.",
        "architecture_avoid_academic": "Avoid academic filler and over-engineering.",
        "secondary_step_pipeline": "You are in step 2 of a multi-step pipeline.",
        "avoid_repetition": "Do not repeat the previous explanation unnecessarily.",
        "build_direct_deliverable": "Produce the technical deliverable directly.",
        "build_focus": "Prioritize code, structure, simple tests, and usage.",
        "build_reuse_handoff": "Explicitly reuse the useful constraints, choices, and pitfalls that already appeared in the first call.",
        "build_keep_decisions": "Do not restart from zero if the first call already framed a valid approach.",
        "avoid_repeating_theory": "Avoid repeating all the theory.",
        "do_not_invent": "Do not invent facts.",
        "only_given_sources": "Do not add sources that are not in the context.",
        "cite_only_real_links": "Cite only the links that were actually provided.",
        "be_concise": "Be concise.",
        "web_no_reproduce_meta": "Do not reproduce the technical result summary in your response; it is provided for your internal context only.",
        "latest_recent_news_1": "The request is about recent news.",
        "latest_recent_news_2": "Today's date: {today}.",
        "latest_recent_news_3": "Do not use a generic page, section page, homepage, or evergreen content as evidence of a current news fact.",
        "latest_recent_news_4": "The sources below were already filtered: work only from them.",
        "latest_recent_news_5": "If the scope is insufficient, say so clearly instead of inventing a synthesis.",
        "latest_recent_news_6": "Do not include a generic 'useful sources' section: cite only the actual articles present below.",
        "latest_recent_news_7": "Do not include items marked 'to verify', 'seems', or 'might' in the main summary.",
        "latest_recent_news_8": "Do not treat topic pages, tags, sections, op-eds, opinions, dossiers, comparisons, or guides as current news.",
        "latest_recent_news_9": "If the scope is an expanded fallback, briefly say that the coverage is wider and potentially less fresh than the strict window.",
        "meta_total_results": "total results",
        "meta_article_results": "article-like results",
        "meta_generic_results": "generic-page results",
        "meta_dated_results": "dated results",
        "meta_recent_dated_results": "recent dated results",
        "meta_news_like_results": "news-like results",
        "meta_recent_news_like_results": "recent news-like results",
        "meta_selected_results": "results selected for synthesis",
        "meta_scope": "scope",
        "title": "Title",
        "source": "Source",
        "date": "Date",
        "type": "Type",
        "news_like": "News-like",
        "url": "URL",
        "content": "Content",
    },
}


def get_prompt_labels(locale: str = "fr") -> dict[str, str]:
    return PROMPT_LABELS.get(locale, PROMPT_LABELS["fr"])


def build_primary_prompt(
    *,
    agent: str,
    output_format: str,
    message: str,
    task_type: str,
    suppress_code: bool = False,
    locale: str = "fr",
) -> str:
    labels = get_prompt_labels(locale)
    role_prompt = get_agent_system_prompt(agent)

    stage_constraints = [
        labels["respond_in_language"],
        labels["respect_output_format"].format(output_format=output_format),
    ]

    if task_type == "build":
        stage_constraints.extend(
            [
                labels["build_priority"],
                labels["build_deliverable"],
                labels["build_complete_code"],
                labels["build_state_assumptions"],
                labels["build_small_dependencies"],
                labels["build_edge_cases"],
                labels["build_no_intro"],
            ]
        )

    if task_type == "architecture":
        stage_constraints.extend(
            [
                labels["architecture_priority"],
                labels["architecture_avoid_academic"],
            ]
        )

    if task_type == "vision":
        stage_constraints.append(labels["vision_anchor_on_image"])

    if suppress_code:
        stage_constraints.extend(
            [
                labels["primary_step_pipeline"],
                labels["no_final_code_yet"],
                labels["reserve_full_technical_deliverable"],
            ]
        )

    constraints = "\n".join(f"- {item}" for item in stage_constraints)
    output_contract = render_output_contract(task_type, output_format, locale=locale)

    return f"""
{role_prompt}

{labels["stage_constraints"]}
{constraints}

{output_contract}

{labels["user_request"]}
{message}
""".strip()


def build_second_call_prompt(
    *,
    agent: str,
    output_format: str,
    user_question: str,
    primary_output: str,
    requested_task_type: str,
    locale: str = "fr",
) -> str:
    labels = get_prompt_labels(locale)
    role_prompt = get_agent_system_prompt(agent)

    stage_constraints = [
        labels["respond_in_language"],
        labels["respect_output_format"].format(output_format=output_format),
        labels["secondary_step_pipeline"],
        labels["avoid_repetition"],
    ]

    if requested_task_type == "build":
        stage_constraints.extend(
            [
                labels["build_direct_deliverable"],
                labels["build_focus"],
                labels["build_complete_code"],
                labels["build_state_assumptions"],
                labels["build_small_dependencies"],
                labels["build_edge_cases"],
                labels["build_reuse_handoff"],
                labels["build_keep_decisions"],
                labels["avoid_repeating_theory"],
                labels["build_no_intro"],
            ]
        )

    constraints = "\n".join(f"- {item}" for item in stage_constraints)
    output_contract = render_output_contract(
        requested_task_type,
        output_format,
        locale=locale,
    )

    return f"""
{role_prompt}

{labels["stage_constraints"]}
{constraints}

{output_contract}

{labels["initial_request"]}
{user_question}

{labels["first_call_response"]}
{primary_output}
""".strip()


def build_web_synthesis_prompt(
    *,
    agent: str,
    output_format: str,
    user_question: str,
    results: list[dict],
    latest_request: bool = False,
    search_meta: dict | None = None,
    locale: str = "fr",
) -> str:
    labels = get_prompt_labels(locale)
    role_prompt = get_agent_system_prompt(agent)
    today = datetime.now(timezone.utc).date().isoformat()
    search_meta = search_meta or {}

    context_blocks = []
    for item in results[:5]:
        title = (item.get("title") or "")[:160]
        url = (item.get("url") or "")[:240]
        content = (item.get("content") or "")[:500]
        source = (item.get("source") or "")[:80]
        published_at = (item.get("published_at") or "")[:40]
        kind = (item.get("kind") or "article")[:20]
        news_like = labels["yes"] if item.get("news_like") else labels["no"]

        context_blocks.append(
            f'{labels["title"]}: {title}\n'
            f'{labels["source"]}: {source}\n'
            f'{labels["date"]}: {published_at or labels["unknown_date"]}\n'
            f'{labels["type"]}: {kind}\n'
            f'{labels["news_like"]}: {news_like}\n'
            f'{labels["url"]}: {url}\n'
            f'{labels["content"]}: {content}'
        )

    context = "\n---\n".join(context_blocks)

    recency_block = ""
    if latest_request:
        recency_lines = [
            labels["latest_recent_news_1"],
            labels["latest_recent_news_2"].format(today=today),
            labels["latest_recent_news_3"],
            labels["latest_recent_news_4"],
            labels["latest_recent_news_5"],
            labels["latest_recent_news_6"],
            labels["latest_recent_news_7"],
            labels["latest_recent_news_8"],
        ]
        if str(search_meta.get("scope", "normal")).startswith("broad_fallback"):
            recency_lines.append(labels["latest_recent_news_9"])
        recency_block = "\n".join(f"- {line}" for line in recency_lines)

    meta_block = ""
    if search_meta:
        meta_block = f"""
{labels["technical_summary"]}
- {labels["meta_total_results"]} : {search_meta.get('results_count', 0)}
- {labels["meta_article_results"]} : {search_meta.get('article_like_count', 0)}
- {labels["meta_generic_results"]} : {search_meta.get('generic_results_count', 0)}
- {labels["meta_dated_results"]} : {search_meta.get('dated_results_count', 0)}
- {labels["meta_recent_dated_results"]} : {search_meta.get('recent_dated_results_count', 0)}
- {labels["meta_news_like_results"]} : {search_meta.get('news_like_count', 0)}
- {labels["meta_recent_news_like_results"]} : {search_meta.get('recent_news_like_count', 0)}
- {labels["meta_selected_results"]} : {search_meta.get('selected_results_count', len(results))}
- {labels["meta_scope"]} : {search_meta.get('scope', 'normal')}
""".strip()

    output_contract = render_output_contract(
        "web_research",
        output_format,
        locale=locale,
    )

    return f"""
{role_prompt}

{labels["stage_constraints"]}
- {labels["respond_in_language"]}
- {labels["respect_output_format"].format(output_format=output_format)}
- {labels["do_not_invent"]}
- {labels["only_given_sources"]}
- {labels["cite_only_real_links"]}
- {labels["be_concise"]}
- {labels["web_no_reproduce_meta"]}
{recency_block}

{output_contract}

{labels["user_question"]}
{user_question}

{meta_block}

{labels["web_results"]}
{context}
""".strip()