from __future__ import annotations

import dataclasses
import os
from datetime import datetime, timezone
from pathlib import Path

from app.clients.blender_client import (
    PIPELINE_PATH_LEGACY,
    build_blender_script,
    run_blender_script,
)
from app.clients.comfyui_client import (
    COMFYUI_OUTPUT_DIR,
    build_visual_request_from_text,
    run_comfyui_workflow,
)
from app.clients.ollama_client import generate_with_ollama
from app.clients.web_client import (
    is_latest_news_query,
    prepare_search_query,
    search_web,
)
from app.engine.comfyui_manifest import write_comfyui_manifest
from app.engine.fallbacks import fallback_text_for_step_error
from app.engine.planner_types import ExecutionState, PlanStep, StepResult
from app.engine.prompt_builder import (
    build_primary_prompt,
    build_second_call_prompt,
    build_web_synthesis_prompt,
)
from app.engine.task_routing import TASK_ROUTING
from app.engine.visual_types import VisualRequest
from app.engine.visual_workflow_selector import analyze_visual_intent


FALLBACK_RECENT_NEWS_MAX_AGE_DAYS = 30
MIN_RECENT_NEWS_RESULTS = 2


def _blender_runtime_max_attempts() -> int:
    """Nombre total d'exécutions Blender pour un step tool_blender (1 + retries).
    Le chemin legacy LLM peut produire du code qui PARSE mais crashe à l'exécution
    (mauvais kwargs bpy) ; on régénère + ré-exécute. Configurable via
    BLENDER_RUNTIME_MAX_ATTEMPTS (défaut 3)."""
    raw = (os.getenv("BLENDER_RUNTIME_MAX_ATTEMPTS") or "3").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 3


def _is_llm_runtime_failure(request, result) -> bool:
    """True si un script du chemin LEGACY a crashé à l'exécution Blender (returncode
    != 0 grâce à --python-exit-code). Régénérer peut alors aider (tirage LLM différent).
    Exclut : le builder déterministe (même IR → même crash), les refus security
    (blocked_security, déjà couvert par le retry de parse), les timeouts, et
    'blender_not_found'."""
    return (
        getattr(request, "pipeline_path", None) == PIPELINE_PATH_LEGACY
        and result.status == "error"
        and result.returncode not in (None, 0)
    )

STEP_EXECUTOR_LABELS = {
    "fr": {
        "no_recent_news": "Je n'ai pas trouvé assez d'articles d'actualité récents et fiables pour fournir une synthèse propre des dernières news IA.",
        "why": "Pourquoi :",
        "results_count": "résultats récupérés",
        "article_like_count": "pages article exploitables",
        "recent_dated_results": "résultats datés récents",
        "recent_news_like_results": "résultats vraiment 'actualité' récents",
        "minimum_required": "minimum requis pour synthèse",
        "sources_collected": "Sources récupérées :",
        "untitled": "Sans titre",
        "unknown_source": "source inconnue",
        "unknown_date": "date inconnue",
        "unknown_kind": "inconnu",
        "yes": "oui",
        "no": "non",
        "recommended_action": "Action conseillée : relancer la recherche avec des requêtes plus ciblées article par article, ou des mots-clés d'annonce/produit/entreprise plus précis.",
        "no_web_results": "Aucun résultat web trouvé.",
        "no_usable_web_results": "Je n'ai pas de résultats web exploitables à synthétiser.",
        "image_generated_success_single": "1 image générée avec succès.",
        "image_generated_success_multi": "{completed_variants} variantes générées avec succès sur {variants_count}.",
        "image_generated_partial": "{completed_variants} variantes générées sur {variants_count}.",
        "image_generated_workflow": "Workflow utilisé : {workflow_id}.",
        "files_generated": "Fichiers générés : {completed_variants}.",
        "primary_file": "Fichier principal : {output_path}",
        "no_visual_output": "ComfyUI a terminé, mais aucun fichier de sortie exploitable n'a été détecté.",
    },
    "en": {
        "no_recent_news": "I could not find enough recent and reliable news articles to produce a clean AI news summary.",
        "why": "Why:",
        "results_count": "results retrieved",
        "article_like_count": "usable article pages",
        "recent_dated_results": "recent dated results",
        "recent_news_like_results": "recent true news-like results",
        "minimum_required": "minimum required for synthesis",
        "sources_collected": "Sources collected:",
        "untitled": "Untitled",
        "unknown_source": "unknown source",
        "unknown_date": "unknown date",
        "unknown_kind": "unknown",
        "yes": "yes",
        "no": "no",
        "recommended_action": "Recommended action: rerun the search with more targeted article-by-article queries, or use more precise announcement/product/company keywords.",
        "no_web_results": "No web results found.",
        "no_usable_web_results": "I do not have usable web results to synthesize.",
        "image_generated_success_single": "1 image generated successfully.",
        "image_generated_success_multi": "{completed_variants} variants generated successfully out of {variants_count}.",
        "image_generated_partial": "{completed_variants} variants generated out of {variants_count}.",
        "image_generated_workflow": "Workflow used: {workflow_id}.",
        "files_generated": "Files generated: {completed_variants}.",
        "primary_file": "Primary file: {output_path}",
        "no_visual_output": "ComfyUI completed, but no usable output file was detected.",
    },
}


def get_step_executor_labels(locale: str = "fr") -> dict[str, str]:
    return STEP_EXECUTOR_LABELS.get(locale, STEP_EXECUTOR_LABELS["fr"])


def get_execution_locale(state: ExecutionState) -> str:
    return (
        state.context.get("response_locale")
        or state.decision.get("response_locale")
        or "fr"
    )


def _should_suppress_code_in_primary(
    state: ExecutionState,
    current_step_id: str,
) -> bool:
    for step in state.plan.steps:
        if step.step_id == current_step_id:
            continue
        if (
            step.step_type == "llm_secondary"
            and step.meta.get("requested_task_type") == "build"
        ):
            return True
    return False


def _parse_result_datetime(value: str | None) -> datetime | None:
    if not value:
        return None

    text = value.strip()
    if not text:
        return None

    candidates = [text]
    if text.endswith("Z"):
        candidates.append(text[:-1] + "+00:00")

    for candidate in candidates:
        try:
            parsed = datetime.fromisoformat(candidate)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            pass

    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            continue

    return None


def _build_web_search_meta(results: list[dict], user_question: str) -> dict:
    latest_request = is_latest_news_query(user_question)
    now = datetime.now(timezone.utc)

    article_like_count = 0
    generic_results_count = 0
    dated_results_count = 0
    recent_dated_results_count = 0
    news_like_count = 0
    recent_news_like_count = 0

    for item in results:
        kind = item.get("kind")
        if kind == "generic":
            generic_results_count += 1
        else:
            article_like_count += 1

        is_news_like = bool(item.get("news_like"))
        if is_news_like:
            news_like_count += 1

        parsed_date = _parse_result_datetime(item.get("published_at"))
        if parsed_date is not None:
            dated_results_count += 1
            age_days = (now - parsed_date).days
            if 0 <= age_days <= 30:
                recent_dated_results_count += 1
                if is_news_like:
                    recent_news_like_count += 1

    return {
        "latest_request": latest_request,
        "results_count": len(results),
        "article_like_count": article_like_count,
        "generic_results_count": generic_results_count,
        "dated_results_count": dated_results_count,
        "recent_dated_results_count": recent_dated_results_count,
        "news_like_count": news_like_count,
        "recent_news_like_count": recent_news_like_count,
    }


def _summarize_web_item(item: dict) -> dict:
    return {
        "title": item.get("title") or "",
        "url": item.get("url") or "",
        "source": item.get("source") or "",
        "published_at": item.get("published_at"),
        "kind": item.get("kind") or "unknown",
        "news_like": bool(item.get("news_like")),
    }


def _is_article_like(item: dict) -> bool:
    return (item.get("kind") or "unknown") != "generic"


def _is_recent_latest_item(
    item: dict,
    *,
    now: datetime,
    max_age_days: int = 21,
) -> bool:
    parsed_date = _parse_result_datetime(item.get("published_at"))
    if parsed_date is None:
        return False

    age_days = (now - parsed_date).days
    return 0 <= age_days <= max_age_days


def _select_web_results_for_synthesis(
    results: list[dict],
    user_question: str,
) -> tuple[list[dict], dict]:
    meta = _build_web_search_meta(results, user_question)
    latest_request = bool(meta.get("latest_request"))
    now = datetime.now(timezone.utc)

    kept_results: list[dict] = []
    dropped_results: list[dict] = []
    selected_results: list[dict] = []

    if not latest_request:
        for item in results[:5]:
            kept_results.append(
                {
                    **_summarize_web_item(item),
                    "kept_reason": "standard_web_result",
                }
            )
            selected_results.append(item)

        meta.update(
            {
                "selection_mode": "standard",
                "selected_results_count": len(selected_results),
                "recent_article_count": 0,
                "required_recent_news_count": 0,
                "scope": "normal",
                "kept_results": kept_results,
                "dropped_results": dropped_results,
            }
        )
        return selected_results, meta

    strict_recent_news_results = []
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
    )
    return selected_results, meta


def _build_latest_news_insufficient_output(
    results: list[dict],
    meta: dict,
    locale: str = "fr",
) -> str:
    labels = get_step_executor_labels(locale)

    lines = [
        labels["no_recent_news"],
        "",
        labels["why"],
        f"- {labels['results_count']} : {meta.get('results_count', 0)}",
        f"- {labels['article_like_count']} : {meta.get('article_like_count', 0)}",
        f"- {labels['recent_dated_results']} : {meta.get('recent_dated_results_count', 0)}",
        f"- {labels['recent_news_like_results']} : {meta.get('recent_news_like_count', 0)}",
        f"- {labels['minimum_required']} : {meta.get('required_recent_news_count', MIN_RECENT_NEWS_RESULTS)}",
    ]

    if results:
        lines.extend(["", labels["sources_collected"]])
        for item in results[:3]:
            title = item.get("title") or labels["untitled"]
            source = item.get("source") or item.get("url") or labels["unknown_source"]
            published_at = item.get("published_at") or labels["unknown_date"]
            kind = item.get("kind") or labels["unknown_kind"]
            news_like = labels["yes"] if item.get("news_like") else labels["no"]
            lines.append(
                f"- {title} | {source} | date: {published_at} | type: {kind} | news-like: {news_like}"
            )

    lines.extend(["", labels["recommended_action"]])
    return "\n".join(lines).strip()


def execute_step(state: ExecutionState, step: PlanStep) -> StepResult:
    locale = get_execution_locale(state)

    try:
        if step.step_type == "llm_primary":
            prompt = build_primary_prompt(
                agent=step.agent or "ASSISTANT",
                output_format=step.output_format or "réponse claire",
                message=state.message,
                task_type=state.decision.get("task_type", "explain_basic"),
                suppress_code=_should_suppress_code_in_primary(
                    state,
                    step.step_id,
                ),
                locale=locale,
            )
            output = generate_with_ollama(
                step.model or state.decision["selected_model"],
                prompt,
            )
            return StepResult(
                step_id=step.step_id,
                step_type=step.step_type,
                status="success",
                output=output,
            )

        if step.step_type == "llm_secondary":
            requested_task_type = step.meta["requested_task_type"]
            second_route = TASK_ROUTING[requested_task_type]
            primary_output = state.get_output("step_primary") or ""

            prompt = build_second_call_prompt(
                agent=second_route.primary_agent,
                output_format=second_route.output_format,
                user_question=state.message,
                primary_output=primary_output,
                requested_task_type=requested_task_type,
                locale=locale,
            )
            output = generate_with_ollama(second_route.model, prompt)

            return StepResult(
                step_id=step.step_id,
                step_type=step.step_type,
                status="success",
                output=output,
                meta={"requested_task_type": requested_task_type},
            )

        if step.step_type == "tool_web_search":
            labels = get_step_executor_labels(locale)
            query_info = prepare_search_query(state.message)
            results = search_web(state.message)
            selected_results, search_meta = _select_web_results_for_synthesis(
                results,
                state.message,
            )

            search_meta.update(
                {
                    "original_query": query_info.get("original_query", state.message),
                    "query_used": query_info.get("query_used", state.message),
                    "query_language": query_info.get("language", "unknown"),
                    "query_strategy": query_info.get("query_strategy", "direct"),
                }
            )

            state.context["web_results"] = results
            state.context["web_results_for_synthesis"] = selected_results
            state.context["web_search_meta"] = search_meta

            if not results:
                state.context["web_search_error"] = labels["no_web_results"]
                return StepResult(
                    step_id=step.step_id,
                    step_type=step.step_type,
                    status="error",
                    error=labels["no_web_results"],
                )

            return StepResult(
                step_id=step.step_id,
                step_type=step.step_type,
                status="success",
                output=None,
                meta=search_meta,
            )

        if step.step_type == "llm_synthesis":
            labels = get_step_executor_labels(locale)
            raw_results = state.context.get("web_results", [])
            synthesis_results = state.context.get(
                "web_results_for_synthesis",
                raw_results,
            )
            search_meta = state.context.get("web_search_meta", {})
            latest_request = bool(search_meta.get("latest_request"))

            if not raw_results:
                fallback_output = state.context.get("web_search_error")
                if not fallback_output:
                    fallback_output = labels["no_usable_web_results"]

                return StepResult(
                    step_id=step.step_id,
                    step_type=step.step_type,
                    status="success",
                    output=fallback_output,
                    meta=search_meta,
                )

            if latest_request and search_meta.get("selected_results_count", 0) == 0:
                return StepResult(
                    step_id=step.step_id,
                    step_type=step.step_type,
                    status="success",
                    output=_build_latest_news_insufficient_output(
                        raw_results,
                        search_meta,
                        locale=locale,
                    ),
                    meta=search_meta,
                )

            prompt = build_web_synthesis_prompt(
                agent=step.agent or "ASSISTANT",
                output_format=step.output_format or "synthèse claire",
                user_question=state.message,
                results=synthesis_results,
                latest_request=latest_request,
                search_meta=search_meta,
                locale=locale,
            )
            output = generate_with_ollama(
                step.model or state.decision["selected_model"],
                prompt,
            )

            return StepResult(
                step_id=step.step_id,
                step_type=step.step_type,
                status="success",
                output=output,
                meta=search_meta,
            )

        if step.step_type == "prepare_visual":
            analysis = analyze_visual_intent(state.message)
            visual_request: VisualRequest = build_visual_request_from_text(state.message)
            state.context["visual_request"] = visual_request
            state.context["visual_analysis"] = analysis

            return StepResult(
                step_id=step.step_id,
                step_type=step.step_type,
                status="success",
                output=None,
                meta={
                    "workflow_id": visual_request.workflow_id,
                    "positive_prompt": visual_request.positive_prompt,
                    "workflow_reason": analysis.reason,
                    "subject_type": analysis.subject_type,
                    "render_intent": analysis.render_intent,
                    "style_flags": analysis.style_flags,
                    "subject_scores": analysis.subject_scores,
                    "render_scores": analysis.render_scores,
                    "parameters": visual_request.to_dict(),
                },
            )

        if step.step_type == "tool_comfyui":
            labels = get_step_executor_labels(locale)
            visual_request = state.context.get("visual_request")
            if visual_request is None:
                visual_request = build_visual_request_from_text(state.message)

            # Dossier par run : image + manifest regroupés dans <output>/<request_id>/.
            _run_subfolder = state.context.get("request_id") or "unknown"
            visual_request = dataclasses.replace(visual_request, output_subfolder=_run_subfolder)

            comfyui_started = datetime.now(timezone.utc)
            result = run_comfyui_workflow(visual_request)
            comfyui_finished = datetime.now(timezone.utc)
            output_path = result.get("output_path") if isinstance(result, dict) else None
            meta = result if isinstance(result, dict) else {}

            # Registre d'exécution ComfyUI dans le volume de sortie — analogue du
            # manifest Blender : route des étapes (avec timings) + OS hôte. Non bloquant.
            try:
                _started_iso = comfyui_started.isoformat()
                _finished_iso = comfyui_finished.isoformat()
                _duration_ms = max(0, int((comfyui_finished - comfyui_started).total_seconds() * 1000))
                _route = [
                    {
                        "step": r.step_id,
                        "type": r.step_type,
                        "status": r.status,
                        "started_at": r.started_at,
                        "finished_at": r.finished_at,
                        "duration_ms": r.duration_ms,
                    }
                    for r in state.step_results
                ]
                _route.append(
                    {
                        "step": step.step_id,
                        "type": step.step_type,
                        "status": "success" if output_path else (meta.get("status") or "error"),
                        "started_at": _started_iso,
                        "finished_at": _finished_iso,
                        "duration_ms": _duration_ms,
                    }
                )
                _manifest_path = write_comfyui_manifest(
                    _run_subfolder,
                    meta,
                    output_dir=(str(Path(COMFYUI_OUTPUT_DIR) / _run_subfolder) if COMFYUI_OUTPUT_DIR else None),
                    timing={
                        "started_at": _started_iso,
                        "finished_at": _finished_iso,
                        "duration_ms": _duration_ms,
                    },
                    route=_route,
                )
                if _manifest_path:
                    meta = {**meta, "manifest_path": _manifest_path}
            except Exception as _manifest_exc:  # noqa: BLE001
                print(f"[comfyui_manifest] hook failed (non-blocking): {_manifest_exc}")
            completed_variants = meta.get("completed_variants") or (1 if output_path else 0)
            variants_count = meta.get("variants_count") or 1
            workflow_id = meta.get("workflow_id")
            partial = bool(meta.get("partial"))
            result_status = meta.get("status") or "success"
            result_error = meta.get("error")

            if output_path:
                lines = []
                if variants_count == 1:
                    lines.append(labels["image_generated_success_single"])
                elif partial:
                    lines.append(
                        labels["image_generated_partial"].format(
                            completed_variants=completed_variants,
                            variants_count=variants_count,
                        )
                    )
                else:
                    lines.append(
                        labels["image_generated_success_multi"].format(
                            completed_variants=completed_variants,
                            variants_count=variants_count,
                        )
                    )

                if workflow_id:
                    lines.append(labels["image_generated_workflow"].format(workflow_id=workflow_id))

                lines.append(labels["files_generated"].format(completed_variants=completed_variants))
                lines.append(labels["primary_file"].format(output_path=output_path))

                return StepResult(
                    step_id=step.step_id,
                    step_type=step.step_type,
                    status="success",
                    output="\n".join(lines),
                    meta=meta,
                )

            return StepResult(
                step_id=step.step_id,
                step_type=step.step_type,
                status="error" if result_status == "error" else "success",
                output=result_error or labels["no_visual_output"],
                error=result_error,
                meta=meta,
            )

        if step.step_type == "prepare_blender_script":
            request_id = (
                state.context.get("request_id")
                or state.decision.get("request_id")
                or str(__import__("uuid").uuid4())
            )
            blender_request = build_blender_script(state.message, state.context, request_id)
            state.context["blender_request"] = blender_request
            return StepResult(
                step_id=step.step_id,
                step_type=step.step_type,
                status="success",
                output=None,
                meta={
                    "request_id": blender_request.request_id,
                    "script_path": blender_request.script_path,
                    "output_path": blender_request.output_path,
                    "output_dir": blender_request.output_dir,
                },
            )

        if step.step_type == "tool_blender":
            blender_request = state.context.get("blender_request")
            if blender_request is None:
                return StepResult(
                    step_id=step.step_id,
                    step_type=step.step_type,
                    status="error",
                    error="blender_request manquant dans le contexte d'exécution.",
                )

            result = run_blender_script(blender_request)

            # Retry runtime (chemin LEGACY uniquement) : le script généré peut
            # PARSER mais crasher à l'exécution (mauvais kwargs bpy). Avec
            # --python-exit-code 1, un crash donne result.status="error" +
            # returncode != 0 ; on régénère un nouveau script (même request_id ->
            # écrase proprement le run) puis on ré-exécute, borné. Le builder
            # déterministe (product_render) re-crasherait à l'identique -> exclu.
            _max_runtime = _blender_runtime_max_attempts()
            _attempt = 1
            while _attempt < _max_runtime and _is_llm_runtime_failure(blender_request, result):
                _attempt += 1
                print(
                    f"[step_executor] tool_blender: crash runtime legacy "
                    f"(returncode={result.returncode}) -> regen+rerun {_attempt}/{_max_runtime}"
                )
                blender_request = build_blender_script(
                    state.message, state.context, blender_request.request_id
                )
                state.context["blender_request"] = blender_request
                result = run_blender_script(blender_request)

            status = "success" if result.status == "success" else "error"

            if result.status == "success":
                output_text = (
                    f"Fichier .blend produit : {result.output_path}\n"
                    f"Script utilisé : {result.script_path}"
                )
            else:
                output_text = (
                    f"Blender terminé avec erreur : {result.status}"
                    + (f"\n{result.error}" if result.error else "")
                )

            return StepResult(
                step_id=step.step_id,
                step_type=step.step_type,
                status=status,
                output=output_text,
                error=result.error if status == "error" else None,
                meta={
                    "status": result.status,
                    "request_id": result.request_id,
                    "script_path": result.script_path,
                    "output_path": result.output_path,
                    "render_path": result.render_path,
                    "output_dir": result.output_dir,
                    "returncode": result.returncode,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "error": result.error,
                    "scene_report": result.scene_report,
                    "scene_report_path": result.scene_report_path,
                    "manifest_path": result.manifest_path,
                },
            )

        return StepResult(
            step_id=step.step_id,
            step_type=step.step_type,
            status="error",
            error=f"Unknown step_type: {step.step_type}",
        )

    except Exception as exc:
        fallback_output = fallback_text_for_step_error(
            step.step_type,
            step.tool,
            str(exc),
            locale=locale,
        )

        if step.step_type == "tool_web_search":
            state.context["web_search_error"] = fallback_output or str(exc)
            state.context.setdefault(
                "web_search_meta",
                _build_web_search_meta([], state.message),
            )

        return StepResult(
            step_id=step.step_id,
            step_type=step.step_type,
            status="error",
            output=fallback_output,
            error=str(exc),
        )