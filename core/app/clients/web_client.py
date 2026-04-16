from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests

from app.infra.runtime_urls import get_searxng_search_url

RECENCY_MARKERS = (
    "derni",
    "latest",
    "recent",
    "récent",
    "aujourd",
    "today",
    "news",
    "actualit",
    "nouvelle",
)

GENERIC_SEGMENTS = {
    "actualites",
    "actualite",
    "news",
    "intelligence-artificielle",
    "intelligence-artificielle/",
    "internet",
    "tech",
    "technologie",
    "ia",
    "ai",
    "rubrique",
    "tag",
    "topic",
    "topics",
    "categorie",
    "category",
    "dossier",
    "direct",
    "sujet",
    "sujets",
}

GENERIC_TITLE_MARKERS = (
    "actualité",
    "actualités",
    "actualite",
    "news",
    "direct",
    "rubrique",
    "dossier",
    "toute l'actualité",
    "infos en direct",
    "en direct",
    "intelligence artificielle",
)

NON_NEWS_TITLE_MARKERS = (
    "meilleur",
    "meilleurs",
    "best",
    "guide",
    "guides",
    "blog",
    "blogs",
    "comparatif",
    "comparatifs",
    "tutoriel",
    "tutoriels",
    "tutorial",
    "tutorials",
    "définition",
    "definition",
    "qu'est-ce",
    "qu est ce",
    "what is",
    "pour s'informer",
    "to know",
    "astuce",
    "astuces",
    "conseil",
    "conseils",
    "outil",
    "outils",
    "top ",
    "liste des",
    "liste de",
    "weather",
    "météo",
    "tribune",
    "tribunes",
    "opinion",
    "opinions",
    "édito",
    "editorial",
    "toute l'actualité",
    "en direct",
)

NON_NEWS_URL_SEGMENTS = {
    "guide",
    "guides",
    "blog",
    "blogs",
    "comparatif",
    "comparatifs",
    "tutoriel",
    "tutoriels",
    "definition",
    "definitions",
    "best",
    "top",
    "tips",
    "outils",
    "outil",
    "weather",
    "tribune",
    "tribunes",
    "opinion",
    "opinions",
    "editorial",
    "edito",
    "sujet",
    "sujets",
}

NON_NEWS_HOST_MARKERS = (
    "sitew.",
    "medium.com",
    "blogspot.",
    "substack.com",
)

QUERY_FR_MARKERS = (
    "cherche",
    "recherche",
    "trouve",
    "trouver",
    "derni",
    "actualité",
    "actualités",
    "article",
    "articles",
    "moi",
    "les",
    "sur",
)

QUERY_STOPWORDS_FR = {
    "cherche",
    "recherche",
    "trouve",
    "trouver",
    "moi",
    "les",
    "des",
    "du",
    "de",
    "la",
    "le",
    "sur",
    "pour",
    "avec",
    "derniere",
    "dernieres",
    "derniers",
    "dernières",
    "news",
    "actualité",
    "actualités",
    "actualite",
    "nouvelles",
    "nouvelle",
    "article",
    "articles",
    "ia",
}

QUERY_STOPWORDS_EN = {
    "latest",
    "recent",
    "news",
    "article",
    "articles",
    "about",
    "for",
    "the",
    "a",
    "an",
    "ai",
    "find",
    "show",
    "me",
}

DATE_PATTERNS = (
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
    re.compile(r"\b\d{2}/\d{2}/\d{4}\b"),
    re.compile(r"\b\d{2}-\d{2}-\d{4}\b"),
)

LISTICLE_PATTERN = re.compile(r"\b\d+\s+(meilleur|meilleurs|best|blogs?|sites?|outils?)\b")


TOKEN_PATTERN = re.compile(r"[a-zA-ZÀ-ÿ0-9][a-zA-ZÀ-ÿ0-9\-\+\.]*")
STANDALONE_IA_PATTERN = re.compile(r"\bia\b", flags=re.IGNORECASE)
STANDALONE_AI_PATTERN = re.compile(r"\bai\b", flags=re.IGNORECASE)


class WebSearchClientError(RuntimeError):
    pass


def is_latest_news_query(query: str) -> bool:
    lowered = (query or "").lower()
    return any(marker in lowered for marker in RECENCY_MARKERS)


def _looks_like_french_query(query: str) -> bool:
    lowered = (query or "").lower()
    return any(marker in lowered for marker in QUERY_FR_MARKERS) or any(ch in lowered for ch in "éèêàùçôîï")


def _safe_text(value: object, max_len: int) -> str:
    text = str(value or "").strip()
    return text[:max_len]


def _extract_source(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def _extract_date_text(item: dict) -> str | None:
    for key in (
        "publishedDate",
        "published_date",
        "published",
        "pubDate",
        "date",
        "created_at",
        "updated_at",
    ):
        value = item.get(key)
        if value:
            return str(value)

    combined = " ".join(
        [
            _safe_text(item.get("title"), 200),
            _safe_text(item.get("content"), 300),
        ]
    )
    for pattern in DATE_PATTERNS:
        match = pattern.search(combined)
        if match:
            return match.group(0)
    return None


def _parse_datetime(value: str | None) -> datetime | None:
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


def _normalize_path_segment(segment: str) -> str:
    lowered = (segment or "").lower().strip()
    if "." in lowered:
        lowered = lowered.split(".", 1)[0]
    return lowered


def _looks_like_generic_page(url: str, title: str) -> bool:
    if not url:
        return True

    parsed = urlparse(url)
    raw_segments = [segment for segment in parsed.path.split("/") if segment.strip()]
    segments = [_normalize_path_segment(segment) for segment in raw_segments]
    lowered_title = (title or "").lower()

    if not segments:
        return True

    if len(segments) <= 2:
        if all(segment in GENERIC_SEGMENTS for segment in segments):
            return True
        if any(marker in lowered_title for marker in GENERIC_TITLE_MARKERS) and all("-" not in segment and not any(ch.isdigit() for ch in segment) for segment in segments):
            return True
        if len(segments) == 1 and segments[0] in GENERIC_SEGMENTS:
            return True

    return False




def _segments_contain_marker(segments: list[str], markers: set[str]) -> bool:
    for segment in segments:
        if segment in markers:
            return True
        for marker in markers:
            if marker and marker in segment:
                return True
    return False

def _looks_like_news_result(url: str, title: str, content: str) -> bool:
    lowered_title = (title or "").lower()
    lowered_content = (content or "").lower()
    parsed = urlparse(url or "")
    segments = [_normalize_path_segment(segment) for segment in parsed.path.split("/") if segment.strip()]
    source = _extract_source(url)

    if not url or _looks_like_generic_page(url, title):
        return False

    if any(marker in source for marker in NON_NEWS_HOST_MARKERS):
        return False

    if any(marker in lowered_title for marker in NON_NEWS_TITLE_MARKERS):
        return False

    if LISTICLE_PATTERN.search(lowered_title):
        return False

    if _segments_contain_marker(segments, NON_NEWS_URL_SEGMENTS):
        return False

    combined = f"{lowered_title} {lowered_content}"
    if "pour s'informer" in combined or "best of" in combined:
        return False

    return True


def _normalize_result(item: dict) -> dict:
    title = _safe_text(item.get("title"), 160)
    url = _safe_text(item.get("url"), 300)
    content = _safe_text(item.get("content"), 600)
    published_at = _extract_date_text(item)
    published_dt = _parse_datetime(published_at)
    kind = "generic" if _looks_like_generic_page(url, title) else "article"
    source = _extract_source(url)
    news_like = _looks_like_news_result(url, title, content)

    return {
        "title": title,
        "url": url,
        "content": content,
        "source": source,
        "published_at": published_at,
        "published_ts": published_dt.timestamp() if published_dt else None,
        "kind": kind,
        "news_like": news_like,
    }


def _sort_key(result: dict) -> tuple[int, int, int, float]:
    kind_penalty = 0 if result.get("kind") == "article" else 1
    news_penalty = 0 if result.get("news_like") else 1
    published_ts = result.get("published_ts")
    missing_date_penalty = 0 if published_ts is not None else 1
    sort_ts = -(published_ts or 0.0)
    return (kind_penalty, news_penalty, missing_date_penalty, sort_ts)


def _dedupe_terms(terms: list[str]) -> list[str]:
    seen = set()
    output: list[str] = []
    for term in terms:
        cleaned = (term or "").strip()
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        output.append(cleaned)
    return output


def _extract_focus_terms(query: str, *, french: bool) -> list[str]:
    lowered = (query or "").lower()
    stopwords = QUERY_STOPWORDS_FR if french else QUERY_STOPWORDS_EN
    terms: list[str] = []
    for token in TOKEN_PATTERN.findall(lowered):
        cleaned = token.strip("-").strip()
        if len(cleaned) < 3:
            continue
        if cleaned in stopwords:
            continue
        if cleaned in {"intelligence", "artificielle", "artificial", "news"}:
            continue
        terms.append(cleaned)
    return _dedupe_terms(terms)


def prepare_search_query(query: str) -> dict:
    original_query = (query or "").strip()
    latest_request = is_latest_news_query(original_query)
    french = _looks_like_french_query(original_query)

    expanded_query = original_query
    had_short_ai_alias = False
    if french and STANDALONE_IA_PATTERN.search(expanded_query):
        expanded_query = STANDALONE_IA_PATTERN.sub("intelligence artificielle", expanded_query)
        had_short_ai_alias = True
    elif not french and STANDALONE_AI_PATTERN.search(expanded_query):
        expanded_query = STANDALONE_AI_PATTERN.sub("artificial intelligence", expanded_query)
        had_short_ai_alias = True

    if not latest_request:
        return {
            "original_query": original_query,
            "query_used": expanded_query.strip() or original_query,
            "latest_request": latest_request,
            "language": "fr" if french else "en",
            "query_strategy": "direct",
            "focus_terms": _extract_focus_terms(expanded_query, french=french),
            "broad_latest_query": False,
            "topic_phrase": None,
        }

    focus_terms = _extract_focus_terms(expanded_query, french=french)
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
        query_terms.extend(["-Iowa", '-"Des Moines"', "-weather"])

    query_used = " ".join(_dedupe_terms(query_terms)).strip() or expanded_query.strip() or original_query

    return {
        "original_query": original_query,
        "query_used": query_used,
        "latest_request": latest_request,
        "language": "fr" if french else "en",
        "query_strategy": "latest_news_rewrite",
        "focus_terms": focus_terms,
        "broad_latest_query": len(focus_terms) <= 1,
        "topic_phrase": topic_phrase,
    }


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


def search_web(query: str) -> list[dict]:
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
    return aggregated[:5]
