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

# Classification rules:
#   ACTIVE_RUNTIME_MODULES   -> code that carries the decision -> plan -> exec -> output flow
#                               (classifier, routing, planner, executor, step, assembly,
#                                prompt/contract layer used by the executor, visual flow pieces)
#   ACTIVE_AUXILIARY_MODULES -> technical support code used *by* the runtime but not itself
#                               part of the decision flow (clients, healthchecks, URL resolution)
#   DORMANT_MODULES          -> present in the repo but not imported by the runtime flow
#                               (superseded internals, legacy snapshots, unused helpers)
#
# Any app/*.py (except __init__.py) must appear in exactly one of these three lists.
# This is enforced by tests/test_runtime_debug_classification.py.

ACTIVE_RUNTIME_MODULES = [
    "app/main.py",
    "app/schemas.py",
    "app/task_classifier.py",
    "app/tool_selector.py",
    "app/engine/task_routing.py",
    "app/engine/routing_conditions.py",
    "app/engine/router_service.py",
    "app/engine/planner_service.py",
    "app/engine/plan_builder.py",
    "app/engine/planner_types.py",
    "app/engine/state_store.py",
    "app/engine/executor.py",
    "app/engine/step_executor.py",
    "app/engine/fallbacks.py",
    "app/engine/prompt_builder.py",
    "app/engine/agent_prompt_registry.py",
    "app/engine/output_contracts.py",
    "app/engine/blender_script_quality.py",
    "app/engine/blender_types.py",
    "app/engine/blender_blocking_contract.py",
    "app/engine/blender_templates.py",
    "app/engine/result_assembler.py",
    "app/engine/runtime_debug.py",
    "app/engine/visual_workflow_selector.py",
    "app/engine/visual_types.py",
]

ACTIVE_AUXILIARY_MODULES = [
    "app/clients/ollama_client.py",
    "app/clients/web_client.py",
    "app/clients/comfyui_client.py",
    "app/clients/comfyui_runtime.py",
    "app/clients/blender_client.py",
    "app/infra/runtime_urls.py",
    "app/infra/tool_manager.py",
]

DORMANT_MODULES = [
    "app/engine/planner.py",
    "app/infra/comfyui_launcher.py",
    "app/model_selector.py",
    "app/agents/prompts.py",
    "app/legacy/task_classifierV1.py",
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
