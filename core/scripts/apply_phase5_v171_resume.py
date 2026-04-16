from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def write(rel: str, content: str) -> None:
    (ROOT / rel).write_text(content, encoding="utf-8", newline="\n")


def swap(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    if old not in text:
        raise RuntimeError(f"[FAIL] snippet not found for {label}")
    return text.replace(old, new, 1)


def insert_after(text: str, anchor: str, block: str, label: str) -> str:
    if block.strip() in text:
        return text
    idx = text.find(anchor)
    if idx == -1:
        raise RuntimeError(f"[FAIL] anchor not found for {label}")
    idx += len(anchor)
    return text[:idx] + block + text[idx:]


def append_if_missing(text: str, marker: str, block: str, label: str) -> str:
    if block.strip() in text:
        return text
    if marker not in text:
        raise RuntimeError(f"[FAIL] marker not found for {label}")
    return text + block


def main() -> int:
    # 1) agent_prompt_registry.py
    rel = "app/engine/agent_prompt_registry.py"
    txt = read(rel)
    txt = swap(
        txt,
        '        "Tu transformes une demande en livrable concret, testable, minimal et utile."',
        '        "Tu transformes une demande en livrable concret, testable, minimal, complet et directement utilisable."',
        rel,
    )
    write(rel, txt)

    # 2) output_contracts.py
    rel = "app/engine/output_contracts.py"
    txt = read(rel)
    txt = swap(
        txt,
        '''        "rules": [
            "Le livrable doit être directement exploitable.",
            "Place le code dans un bloc de code complet.",
            "Ajoute des tests ou vérifications rapides minimales.",
        ],''',
        '''        "rules": [
            "Le livrable doit être directement exploitable.",
            "Place le code dans un bloc de code complet.",
            "Le code doit être cohérent, copiable-collable et sans TODO, pseudo-code ou trous.",
            "Indique explicitement les hypothèses minimales si certaines entrées ne sont pas précisées.",
            "Ajoute des tests ou vérifications rapides minimales.",
        ],''',
        rel,
    )
    write(rel, txt)

    # 3) prompt_builder.py
    rel = "app/engine/prompt_builder.py"
    txt = read(rel)

    txt = insert_after(
        txt,
        '        "build_deliverable": "Donne directement un résultat exploitable, avec structure, tests simples et usage.",\n',
        '        "build_complete_code": "Le code doit être complet, cohérent, exécutable et sans TODO ni pseudo-code.",\n'
        '        "build_state_assumptions": "Si une hypothèse est nécessaire, écris-la explicitement puis continue avec la version minimale raisonnable.",\n'
        '        "build_small_dependencies": "Évite les dépendances inutiles ; préfère la bibliothèque standard sauf demande contraire.",\n'
        '        "build_edge_cases": "Prévois un minimum de validation d\'entrée, d\'erreur simple ou de garde-fous utiles.",\n',
        rel + " fr build labels",
    )

    txt = insert_after(
        txt,
        '        "build_focus": "Privilégie le code, la structure, les tests simples et l\'usage.",\n',
        '        "build_reuse_handoff": "Réutilise explicitement les contraintes, choix utiles et pièges déjà apparus dans le premier appel.",\n'
        '        "build_keep_decisions": "Ne repars pas de zéro si le premier appel a déjà cadré une approche valable.",\n',
        rel + " fr handoff labels",
    )

    txt = insert_after(
        txt,
        '        "latest_recent_news_8": "N\'utilise pas comme actualités des pages sujet, des tags, des rubriques, des tribunes, des opinions, des dossiers, des comparatifs ou des guides.",\n',
        '        "latest_recent_news_9": "Si le périmètre est un fallback élargi, signale brièvement que la couverture est plus large et potentiellement moins fraîche que la fenêtre stricte.",\n',
        rel + " fr latest labels",
    )

    txt = insert_after(
        txt,
        '        "build_deliverable": "Provide a directly usable result, with structure, simple tests, and usage.",\n',
        '        "build_complete_code": "The code must be complete, coherent, executable, and contain no TODOs or pseudocode.",\n'
        '        "build_state_assumptions": "If an assumption is required, state it explicitly and continue with the smallest reasonable version.",\n'
        '        "build_small_dependencies": "Avoid unnecessary dependencies; prefer the standard library unless the user asked otherwise.",\n'
        '        "build_edge_cases": "Include minimal input validation, simple error handling, or useful guardrails.",\n',
        rel + " en build labels",
    )

    txt = insert_after(
        txt,
        '        "build_focus": "Prioritize code, structure, simple tests, and usage.",\n',
        '        "build_reuse_handoff": "Explicitly reuse the useful constraints, choices, and pitfalls that already appeared in the first call.",\n'
        '        "build_keep_decisions": "Do not restart from zero if the first call already framed a valid approach.",\n',
        rel + " en handoff labels",
    )

    txt = insert_after(
        txt,
        '        "latest_recent_news_8": "Do not treat topic pages, tags, sections, op-eds, opinions, dossiers, comparisons, or guides as current news.",\n',
        '        "latest_recent_news_9": "If the scope is an expanded fallback, briefly say that the coverage is wider and potentially less fresh than the strict window.",\n',
        rel + " en latest labels",
    )

    txt = swap(
        txt,
        """    if task_type == "build":
        stage_constraints.extend(
            [
                labels["build_priority"],
                labels["build_deliverable"],
            ]
        )""",
        """    if task_type == "build":
        stage_constraints.extend(
            [
                labels["build_priority"],
                labels["build_deliverable"],
                labels["build_complete_code"],
                labels["build_state_assumptions"],
                labels["build_small_dependencies"],
                labels["build_edge_cases"],
            ]
        )""",
        rel + " build primary block",
    )

    txt = swap(
        txt,
        """    if requested_task_type == "build":
        stage_constraints.extend(
            [
                labels["build_direct_deliverable"],
                labels["build_focus"],
                labels["avoid_repeating_theory"],
            ]
        )""",
        """    if requested_task_type == "build":
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
            ]
        )""",
        rel + " build second call block",
    )

    txt = swap(
        txt,
        '        recency_block = "\\n".join(f"- {line}" for line in recency_lines)',
        '        if str(search_meta.get("scope", "normal")).startswith("broad_fallback"):\n'
        '            recency_lines.append(labels["latest_recent_news_9"])\n'
        '        recency_block = "\\n".join(f"- {line}" for line in recency_lines)',
        rel + " latest recency block",
    )

    write(rel, txt)

    # 4) web_client.py
    rel = "app/clients/web_client.py"
    txt = read(rel)

    txt = swap(
        txt,
        """    if not latest_request:
        return {
            "original_query": original_query,
            "query_used": expanded_query.strip() or original_query,
            "latest_request": latest_request,
            "language": "fr" if french else "en",
            "query_strategy": "direct",
        }""",
        """    if not latest_request:
        return {
            "original_query": original_query,
            "query_used": expanded_query.strip() or original_query,
            "latest_request": latest_request,
            "language": "fr" if french else "en",
            "query_strategy": "direct",
            "focus_terms": _extract_focus_terms(expanded_query, french=french),
            "broad_latest_query": False,
            "topic_phrase": None,
        }""",
        rel + " non latest return",
    )

    txt = swap(
        txt,
        """    base_terms = [
        '"intelligence artificielle"',
        "actualités",
        "article",
    ] if french else [
        '"artificial intelligence"',
        "news",
        "article",
    ]

    focus_terms = _extract_focus_terms(expanded_query, french=french)
    query_terms = _dedupe_terms(base_terms + focus_terms[:4])

    if had_short_ai_alias or (french and "intelligence artificielle" in expanded_query.lower()):
        query_terms.extend(["-Iowa", '-"Des Moines"', "-weather"])""",
        """    focus_terms = _extract_focus_terms(expanded_query, french=french)
    lowered_expanded = expanded_query.lower()
    has_ai_topic = had_short_ai_alias or (
        french and "intelligence artificielle" in lowered_expanded
    ) or (
        (not french) and "artificial intelligence" in lowered_expanded
    )

    topic_phrase = None
    if has_ai_topic:
        topic_phrase = '"intelligence artificielle"' if french else '"artificial intelligence"'

    base_terms = []
    if topic_phrase:
        base_terms.append(topic_phrase)
    base_terms.extend(["actualités", "article"] if french else ["news", "article"])

    query_terms = _dedupe_terms(base_terms + focus_terms[:4])

    if has_ai_topic:
        query_terms.extend(["-Iowa", '-"Des Moines"', "-weather"])""",
        rel + " latest rewrite block",
    )

    txt = swap(
        txt,
        """    return {
        "original_query": original_query,
        "query_used": query_used,
        "latest_request": latest_request,
        "language": "fr" if french else "en",
        "query_strategy": "latest_news_rewrite",
    }""",
        """    return {
        "original_query": original_query,
        "query_used": query_used,
        "latest_request": latest_request,
        "language": "fr" if french else "en",
        "query_strategy": "latest_news_rewrite",
        "focus_terms": focus_terms,
        "broad_latest_query": len(focus_terms) <= 1,
        "topic_phrase": topic_phrase,
    }""",
        rel + " latest return",
    )

    txt = insert_after(
        txt,
        """    return {
        "original_query": original_query,
        "query_used": query_used,
        "latest_request": latest_request,
        "language": "fr" if french else "en",
        "query_strategy": "latest_news_rewrite",
        "focus_terms": focus_terms,
        "broad_latest_query": len(focus_terms) <= 1,
        "topic_phrase": topic_phrase,
    }\n""",
        """

def _build_latest_search_variants(query_info: dict) -> list[str]:
    base_query = str(query_info.get("query_used", "")).strip()
    if not base_query:
        return []

    variants = [base_query]
    if not query_info.get("latest_request"):
        return variants

    language = query_info.get("language", "fr")
    focus_terms = list(query_info.get("focus_terms") or [])
    broad_latest = bool(query_info.get("broad_latest_query"))
    topic_phrase = query_info.get("topic_phrase")

    if broad_latest and focus_terms:
        focus_phrase = " ".join(focus_terms[:3]).strip()
        if focus_phrase:
            if language == "fr":
                variants.append(f"{focus_phrase} actualités article")
                variants.append(f"{focus_phrase} annonce article")
            else:
                variants.append(f"{focus_phrase} news article")
                variants.append(f"{focus_phrase} announcement article")

    if broad_latest and topic_phrase:
        if language == "fr":
            variants.append(f"{topic_phrase} actualités article")
        else:
            variants.append(f"{topic_phrase} news article")

    deduped = []
    seen = set()
    for item in variants:
        normalized = " ".join(item.split()).strip().lower()
        if normalized and normalized not in seen:
            seen.add(normalized)
            deduped.append(" ".join(item.split()).strip())
    return deduped
""",
        rel + " insert variant builder",
    )

    txt = swap(
        txt,
        """def search_web(query: str) -> list[dict]:
    query_info = prepare_search_query(query)
    params = {
        "q": query_info["query_used"],
        "format": "json",
    }

    if query_info["latest_request"]:
        params["time_range"] = "week"

    try:
        response = requests.get(get_searxng_search_url(), params=params, timeout=20)
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as exc:
        raise WebSearchClientError(str(exc)) from exc
    except ValueError as exc:
        raise WebSearchClientError(f"Réponse JSON invalide: {exc}") from exc

    results = [_normalize_result(item) for item in data.get("results", [])[:8]]
    results.sort(key=_sort_key)
    return results[:5]""",
        """def search_web(query: str) -> list[dict]:
    query_info = prepare_search_query(query)
    query_variants = _build_latest_search_variants(query_info)
    if not query_variants:
        query_variants = [query_info["query_used"]]

    aggregated = []
    seen_urls = set()

    try:
        for candidate_query in query_variants:
            params = {
                "q": candidate_query,
                "format": "json",
            }

            if query_info["latest_request"]:
                params["time_range"] = "week"

            response = requests.get(get_searxng_search_url(), params=params, timeout=20)
            response.raise_for_status()
            data = response.json()

            for item in data.get("results", [])[:8]:
                normalized = _normalize_result(item)
                url = normalized.get("url") or ""
                if url and url in seen_urls:
                    continue
                if url:
                    seen_urls.add(url)
                aggregated.append(normalized)
    except requests.RequestException as exc:
        raise WebSearchClientError(str(exc)) from exc
    except ValueError as exc:
        raise WebSearchClientError(f"Réponse JSON invalide: {exc}") from exc

    aggregated.sort(key=_sort_key)
    return aggregated[:5]""",
        rel + " search_web body",
    )

    write(rel, txt)

    # 5) step_executor.py
    rel = "app/engine/step_executor.py"
    txt = read(rel)

    txt = swap(
        txt,
        "MIN_RECENT_NEWS_RESULTS = 2\n",
        "FALLBACK_RECENT_NEWS_MAX_AGE_DAYS = 30\nMIN_RECENT_NEWS_RESULTS = 2\n",
        rel + " constants",
    )

    txt = swap(
        txt,
        """    recent_news_results = []

    for item in results:
        base_summary = _summarize_web_item(item)

        if not _is_article_like(item):
            dropped_results.append(
                {
                    **base_summary,
                    "drop_reason": "generic_page_excluded_for_latest",
                }
            )
            continue

        if not item.get("news_like"):
            drop_reason = "not_news_like_article"
            lowered_url = (base_summary.get("url") or "").lower()
            lowered_title = (base_summary.get("title") or "").lower()

            if any(
                marker in lowered_url
                for marker in (
                    "/sujet/",
                    "/tag/",
                    "/topic/",
                    "/dossier/",
                    "/rubrique/",
                    "/actualites/",
                    "/actualite/",
                )
            ):
                drop_reason = "topic_page_excluded_for_latest"
            elif any(
                marker in lowered_title
                for marker in (
                    "toute l'actualité",
                    "toute l actualité",
                    "infos en direct",
                    "en direct",
                    "rubrique",
                    "dossier",
                    "sujet",
                    "tag",
                    "topic",
                )
            ):
                drop_reason = "topic_page_excluded_for_latest"
            elif any(
                marker in lowered_url
                for marker in (
                    "tribune",
                    "tribunes",
                    "opinion",
                    "opinions",
                    "edito",
                    "editorial",
                    "analyse",
                    "analyses",
                    "chronique",
                    "chroniques",
                    "guide",
                    "comparatif",
                    "blogs",
                    "blog",
                    "meilleurs",
                )
            ):
                drop_reason = "editorial_or_evergreen_excluded_for_latest"
            elif any(
                marker in lowered_title
                for marker in (
                    "tribune",
                    "opinion",
                    "édito",
                    "edito",
                    "editorial",
                    "analyse",
                    "chronique",
                    "guide",
                    "comparatif",
                    "meilleurs",
                    "blogs",
                )
            ):
                drop_reason = "editorial_or_evergreen_excluded_for_latest"

            dropped_results.append(
                {
                    **base_summary,
                    "drop_reason": drop_reason,
                }
            )
            continue

        if not _is_recent_latest_item(item, now=now):
            dropped_results.append(
                {
                    **base_summary,
                    "drop_reason": "not_recent_dated_news_article",
                }
            )
            continue

        recent_news_results.append(item)

    if len(recent_news_results) >= MIN_RECENT_NEWS_RESULTS:
        selected_results = recent_news_results[:5]
        scope = "normal"

        for item in selected_results:
            kept_results.append(
                {
                    **_summarize_web_item(item),
                    "kept_reason": "recent_news_article",
                }
            )
    else:
        selected_results = []
        scope = "insufficient"
        kept_results = []

    meta.update(
        {
            "selection_mode": "latest_news_newslike_strict",
            "selected_results_count": len(selected_results),
            "recent_article_count": len(recent_news_results),
            "required_recent_news_count": MIN_RECENT_NEWS_RESULTS,
            "scope": scope,
            "kept_results": kept_results,
            "dropped_results": dropped_results[:5],
        }
    )""",
        """    strict_recent_news_results = []
    fallback_recent_news_results = []

    for item in results:
        base_summary = _summarize_web_item(item)

        if not _is_article_like(item):
            dropped_results.append(
                {
                    **base_summary,
                    "drop_reason": "generic_page_excluded_for_latest",
                }
            )
            continue

        if not item.get("news_like"):
            drop_reason = "not_news_like_article"
            lowered_url = (base_summary.get("url") or "").lower()
            lowered_title = (base_summary.get("title") or "").lower()

            if any(
                marker in lowered_url
                for marker in (
                    "/sujet/",
                    "/tag/",
                    "/topic/",
                    "/dossier/",
                    "/rubrique/",
                    "/actualites/",
                    "/actualite/",
                )
            ):
                drop_reason = "topic_page_excluded_for_latest"
            elif any(
                marker in lowered_title
                for marker in (
                    "toute l'actualité",
                    "toute l actualité",
                    "infos en direct",
                    "en direct",
                    "rubrique",
                    "dossier",
                    "sujet",
                    "tag",
                    "topic",
                )
            ):
                drop_reason = "topic_page_excluded_for_latest"
            elif any(
                marker in lowered_url
                for marker in (
                    "tribune",
                    "tribunes",
                    "opinion",
                    "opinions",
                    "edito",
                    "editorial",
                    "analyse",
                    "analyses",
                    "chronique",
                    "chroniques",
                    "guide",
                    "comparatif",
                    "blogs",
                    "blog",
                    "meilleurs",
                )
            ):
                drop_reason = "editorial_or_evergreen_excluded_for_latest"
            elif any(
                marker in lowered_title
                for marker in (
                    "tribune",
                    "opinion",
                    "édito",
                    "edito",
                    "editorial",
                    "analyse",
                    "chronique",
                    "guide",
                    "comparatif",
                    "meilleurs",
                    "blogs",
                )
            ):
                drop_reason = "editorial_or_evergreen_excluded_for_latest"

            dropped_results.append(
                {
                    **base_summary,
                    "drop_reason": drop_reason,
                }
            )
            continue

        if _is_recent_latest_item(item, now=now):
            strict_recent_news_results.append(item)
            continue

        if _is_recent_latest_item(item, now=now, max_age_days=FALLBACK_RECENT_NEWS_MAX_AGE_DAYS):
            fallback_recent_news_results.append(item)
            continue

        dropped_results.append(
            {
                **base_summary,
                "drop_reason": "not_recent_dated_news_article",
            }
        )

    if len(strict_recent_news_results) >= MIN_RECENT_NEWS_RESULTS:
        selected_results = strict_recent_news_results[:5]
        scope = "normal"
        selection_mode = "latest_news_newslike_strict"
        kept_reason = "recent_news_article"
    elif len(strict_recent_news_results) + len(fallback_recent_news_results) >= MIN_RECENT_NEWS_RESULTS:
        selected_results = (strict_recent_news_results + fallback_recent_news_results)[:5]
        scope = "broad_fallback_30d"
        selection_mode = "latest_news_newslike_fallback_30d"
        kept_reason = "recent_news_article_fallback_30d"
    else:
        selected_results = []
        scope = "insufficient"
        selection_mode = "latest_news_newslike_strict"
        kept_reason = ""

    kept_results = []
    if selected_results:
        for item in selected_results:
            kept_results.append(
                {
                    **_summarize_web_item(item),
                    "kept_reason": kept_reason,
                }
            )

    meta.update(
        {
            "selection_mode": selection_mode,
            "selected_results_count": len(selected_results),
            "recent_article_count": len(strict_recent_news_results),
            "fallback_recent_article_count": len(fallback_recent_news_results),
            "required_recent_news_count": MIN_RECENT_NEWS_RESULTS,
            "fallback_recent_news_max_age_days": FALLBACK_RECENT_NEWS_MAX_AGE_DAYS,
            "scope": scope,
            "kept_results": kept_results,
            "dropped_results": dropped_results[:5],
        }
    )""",
        rel + " latest selection fallback",
    )

    write(rel, txt)

    # 6) tests
    rel = "tests/test_output_contracts_v2a1.py"
    txt = read(rel)
    txt = append_if_missing(
        txt,
        'assert "3. Décision recommandée" in rendered',
        """

def test_build_contract_adds_copy_pasteable_guardrails():
    contract = get_output_contract("build")

    assert any("copiable-collable" in rule.lower() for rule in contract["rules"])
    assert any("hypothèses" in rule.lower() for rule in contract["rules"])
""",
        rel,
    )
    write(rel, txt)

    rel = "tests/test_prompt_contracts_v2a1.py"
    txt = read(rel)
    txt = append_if_missing(
        txt,
        'assert "3. Sources retenues" in prompt',
        """

def test_primary_build_prompt_adds_quality_guardrails():
    prompt = build_primary_prompt(
        agent="AGENT_BUILDER_IA",
        output_format="module python + structure + instructions de test + usage",
        message="écris un parseur csv minimal",
        task_type="build",
    )

    assert "sans TODO ni pseudo-code".lower() in prompt.lower()
    assert "bibliothèque standard".lower() in prompt.lower()


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
""",
        rel,
    )
    write(rel, txt)

    rel = "tests/test_executor_prompt_contracts_v2a1.py"
    txt = read(rel)
    txt = append_if_missing(
        txt,
        'assert "3. Sources retenues" in captured["prompt"]',
        """

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
""",
        rel,
    )
    write(rel, txt)

    rel = "tests/test_web_pipeline_freshness.py"
    txt = read(rel)

    txt = swap(
        txt,
        """    assert query_info["latest_request"] is True
    assert query_info["language"] == "fr"
    assert query_info["query_strategy"] == "latest_news_rewrite"
    assert '"intelligence artificielle"' in query_info["query_used"]
    assert "actualités" in query_info["query_used"]
    assert "article" in query_info["query_used"]
    assert "-Iowa" in query_info["query_used"]""",
        """    assert query_info["latest_request"] is True
    assert query_info["language"] == "fr"
    assert query_info["query_strategy"] == "latest_news_rewrite"
    assert query_info["broad_latest_query"] is True
    assert '"intelligence artificielle"' in query_info["query_used"]
    assert "actualités" in query_info["query_used"]
    assert "article" in query_info["query_used"]
    assert "-Iowa" in query_info["query_used"]""",
        rel + " test 1",
    )

    txt = swap(
        txt,
        """def test_search_web_uses_rewritten_latest_query(monkeypatch):
    captured = {}

    def fake_get(url, params=None, timeout=None):
        captured["url"] = url
        captured["params"] = params or {}
        return _FakeResponse({"results": []})

    monkeypatch.setattr("app.clients.web_client.requests.get", fake_get)

    search_web("cherche moi les dernières news IA")

    assert captured["params"]["time_range"] == "week"
    assert '"intelligence artificielle"' in captured["params"]["q"]
    assert "actualités" in captured["params"]["q"]
    assert "article" in captured["params"]["q"]
    assert "-Iowa" in captured["params"]["q"]""",
        """def test_search_web_uses_rewritten_latest_query(monkeypatch):
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
    assert any("-Iowa" in call["params"]["q"] for call in captured_calls)""",
        rel + " test 2",
    )

    txt = append_if_missing(
        txt,
        "def test_latest_news_pipeline_keeps_only_recent_news_articles_for_prompt",
        """

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
""",
        rel,
    )

    write(rel, txt)

    print("[OK] phase 5 V1.7.1 resumed and applied.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())