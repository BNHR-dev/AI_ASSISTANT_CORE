from __future__ import annotations

from datetime import datetime, timezone

from app.infra.runtime_urls import (
    get_comfyui_url,
    get_ollama_tags_url,
    get_searxng_healthcheck_url,
)
from app.infra.tool_manager import is_comfyui_ready, is_ollama_ready, is_searxng_ready


APP_VERSION = "1.7.0"


CANONICAL_PATHS = [
    "app/*",
    "openai_compat.py",
    "docs/*",
]

LEGACY_SHIMS = [
    "executor.py",
    "router_service.py",
    "task_classifier.py",
    "tool_selector.py",
    "task_routing.py",
    "comfyui_client.py",
]

ACTIVE_RUNTIME_MODULES = [
    "app/main.py",
    "app/engine/router_service.py",
    "app/engine/planner_service.py",
    "app/engine/executor.py",
    "app/engine/step_executor.py",
    "app/engine/result_assembler.py",
]

ACTIVE_AUXILIARY_MODULES = [
    "app/infra/tool_manager.py",
    "app/engine/visual_workflow_selector.py",
    "app/engine/visual_types.py",
]

DORMANT_MODULES = [
    "app/engine/planner.py",
    "app/infra/comfyui_launcher.py",
]

OPTIONAL_RUNTIME_SERVICES = [
    "searxng",
    "comfyui",
]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _build_service_status(
    name: str,
    check_result: tuple[bool, str],
    *,
    role: str,
    required: bool,
    endpoint: str,
) -> dict:
    ready, reason = check_result
    return {
        "name": name,
        "ready": bool(ready),
        "required": required,
        "role": role,
        "reason": reason,
        "endpoint": endpoint,
        "activity": "active" if required else "optional",
    }


def get_runtime_health() -> dict:
    services = {
        "ollama": _build_service_status(
            "ollama",
            is_ollama_ready(),
            role="llm_backend",
            required=True,
            endpoint=get_ollama_tags_url(),
        ),
        "searxng": _build_service_status(
            "searxng",
            is_searxng_ready(),
            role="web_search",
            required=False,
            endpoint=get_searxng_healthcheck_url(),
        ),
        "comfyui": _build_service_status(
            "comfyui",
            is_comfyui_ready(),
            role="visual_generation",
            required=False,
            endpoint=get_comfyui_url(),
        ),
    }

    required_failures = [item["name"] for item in services.values() if item["required"] and not item["ready"]]
    optional_failures = [item["name"] for item in services.values() if not item["required"] and not item["ready"]]

    if required_failures:
        status = "degraded"
        summary = f"core runtime degraded: required backend unavailable: {', '.join(required_failures)}"
    elif optional_failures:
        status = "partial"
        summary = f"core runtime ready; optional services unavailable: {', '.join(optional_failures)}"
    else:
        status = "ok"
        summary = "core runtime ready; optional services reachable"

    return {
        "status": status,
        "version": APP_VERSION,
        "checked_at": _utc_now_iso(),
        "summary": summary,
        "services": services,
    }


def get_canonical_boundaries() -> dict:
    return {
        "status": "ok",
        "version": APP_VERSION,
        "canonical_paths": CANONICAL_PATHS,
        "legacy_shims": LEGACY_SHIMS,
        "active_runtime_modules": ACTIVE_RUNTIME_MODULES,
        "active_auxiliary_modules": ACTIVE_AUXILIARY_MODULES,
        "optional_runtime_services": OPTIONAL_RUNTIME_SERVICES,
        "dormant_modules": DORMANT_MODULES,
        "rule": "app/* defines runtime behavior; legacy root files stay compatibility-only; auxiliary modules are active support code and must not be mislabeled as dormant.",
    }
