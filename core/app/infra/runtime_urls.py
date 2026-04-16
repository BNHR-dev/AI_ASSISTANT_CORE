from __future__ import annotations

import os
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def _clean_url(value: str | None) -> str:
    return (value or "").strip().rstrip("/")


def _ensure_ollama_api_url(url: str, leaf: str) -> str:
    cleaned = _clean_url(url)
    if not cleaned:
        return ""

    for known_leaf in ("generate", "tags"):
        known_suffix = f"/api/{known_leaf}"
        if cleaned.endswith(known_suffix):
            return f"{cleaned[:-len(known_suffix)]}/api/{leaf}"

    suffix = f"/api/{leaf}"
    if cleaned.endswith(suffix):
        return cleaned
    if cleaned.endswith("/api"):
        return f"{cleaned}/{leaf}"
    if cleaned.endswith("/api/"):
        return f"{cleaned}{leaf}"
    return f"{cleaned}{suffix}"


def _with_query(url: str, **params: str) -> str:
    cleaned = _clean_url(url)
    if not cleaned:
        return cleaned

    parsed = urlsplit(cleaned)
    merged = dict(parse_qsl(parsed.query, keep_blank_values=True))
    merged.update({key: value for key, value in params.items() if value is not None})
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(merged), parsed.fragment))


def get_ollama_generate_url() -> str:
    explicit = _clean_url(os.getenv("OLLAMA_GENERATE_URL"))
    if explicit:
        return _ensure_ollama_api_url(explicit, "generate")

    legacy = _clean_url(os.getenv("OLLAMA_URL"))
    if legacy:
        return _ensure_ollama_api_url(legacy, "generate")

    base = _clean_url(os.getenv("OLLAMA_BASE_URL"))
    if base:
        return _ensure_ollama_api_url(base, "generate")

    return "http://localhost:12000/api/generate"


def get_ollama_tags_url() -> str:
    explicit = _clean_url(os.getenv("OLLAMA_TAGS_URL"))
    if explicit:
        return _ensure_ollama_api_url(explicit, "tags")

    return _ensure_ollama_api_url(get_ollama_generate_url(), "tags")


def get_searxng_search_url() -> str:
    explicit = _clean_url(os.getenv("SEARXNG_SEARCH_URL"))
    if explicit:
        if explicit.endswith('/search'):
            return explicit
        return f"{explicit}/search"

    return "http://localhost:8081/search"


def get_searxng_healthcheck_url() -> str:
    return _with_query(get_searxng_search_url(), q='test', format='json')


def get_comfyui_url() -> str:
    return _clean_url(os.getenv("COMFYUI_URL") or "http://127.0.0.1:8188")


def get_comfyui_auto_start() -> bool:
    return (os.getenv("COMFYUI_AUTO_START", "1").strip().lower() not in {"0", "false", "no"})


def get_comfyui_start_timeout() -> int:
    raw = (os.getenv("COMFYUI_START_TIMEOUT") or "120").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 120
