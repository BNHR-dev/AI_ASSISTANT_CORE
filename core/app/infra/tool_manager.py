from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import requests

from app.infra.runtime_urls import (
    get_comfyui_auto_start,
    get_comfyui_start_timeout,
    get_comfyui_url,
    get_ollama_tags_url,
    get_searxng_healthcheck_url,
)

COMFYUI_BAT_PATH = os.getenv("COMFYUI_BAT_PATH", "").strip()
COMFYUI_POLL_INTERVAL = float(os.getenv("COMFYUI_POLL_INTERVAL", "2.0"))


class ToolManagerError(RuntimeError):
    pass


def _http_ok(
    url: str,
    timeout: float = 3.0,
    retries: int = 0,
    retry_delay_s: float = 0.0,
) -> tuple[bool, str]:
    last_exc: requests.RequestException | None = None
    attempts = max(1, retries + 1)

    for attempt in range(attempts):
        try:
            response = requests.get(url, timeout=timeout)
            return response.ok, f"http {response.status_code}"
        except requests.RequestException as exc:
            last_exc = exc
            if attempt < attempts - 1 and retry_delay_s > 0:
                time.sleep(retry_delay_s)

    return False, str(last_exc) if last_exc else "unknown http error"


def is_ollama_ready() -> tuple[bool, str]:
    return _http_ok(get_ollama_tags_url())


def is_searxng_ready() -> tuple[bool, str]:
    return _http_ok(
        get_searxng_healthcheck_url(),
        timeout=4.5,
        retries=1,
        retry_delay_s=1.0,
    )


def is_comfyui_ready() -> tuple[bool, str]:
    """Pure REACHABILITY probe (HTTP). Says nothing about model availability."""
    return _http_ok(get_comfyui_url())


def _comfyui_required_models() -> dict:
    """Configured ComfyUI models bucketed by loader category.

    Resolved from the SAME constants the workflow injection uses, so health validates
    exactly the model names a render will request -> one source of truth, no drift.
    """
    from app.clients.comfyui_client import (
        COMFYUI_CHECKPOINT_NAME,
        COMFYUI_REFINER_CHECKPOINT_NAME,
        COMFYUI_UPSCALE_MODEL_NAME,
    )

    return {
        "checkpoints": {COMFYUI_CHECKPOINT_NAME, COMFYUI_REFINER_CHECKPOINT_NAME},
        "upscale_models": {COMFYUI_UPSCALE_MODEL_NAME},
    }


# (ComfyUI node class, required-input field, bucket) for /object_info introspection.
_COMFYUI_LOADER_FIELDS = (
    ("CheckpointLoaderSimple", "ckpt_name", "checkpoints"),
    ("UpscaleModelLoader", "model_name", "upscale_models"),
)


def _extract_object_info_choices(field_spec):
    """Choices from a ComfyUI /object_info required-input spec.

    ComfyUI mixes two shapes for combo inputs (seen on the same instance):
      legacy : [[choice, ...], {meta}]                  -> choices are field_spec[0] (a list)
      combo  : ["COMBO", {"options": [choice, ...]}]    -> choices are field_spec[1]["options"]
    Returns a list of strings, or None if the spec is not understood.
    """
    if not isinstance(field_spec, list) or not field_spec:
        return None
    head = field_spec[0]
    if isinstance(head, list):
        return [str(x) for x in head]
    if isinstance(head, str) and head.upper() == "COMBO" and len(field_spec) > 1:
        opts = field_spec[1]
        if isinstance(opts, dict) and isinstance(opts.get("options"), list):
            return [str(x) for x in opts["options"]]
    return None


def _comfyui_available_models(timeout: float = 4.0):
    """Models ComfyUI actually exposes via /object_info. None if it cannot be read."""
    base = get_comfyui_url()
    available: dict = {}
    for node_class, field, bucket in _COMFYUI_LOADER_FIELDS:
        try:
            response = requests.get(f"{base}/object_info/{node_class}", timeout=timeout)
        except requests.RequestException:
            return None
        if not response.ok:
            return None
        try:
            data = response.json()
            spec = data[node_class]["input"]["required"][field]
        except (ValueError, KeyError, TypeError):
            return None
        choices = _extract_object_info_choices(spec)
        if choices is None:
            return None
        available[bucket] = set(choices)
    return available


def _ollama_model_present(required: str, installed: set[str]) -> bool:
    """`bge-m3` matche `bge-m3:latest` : un nom requis SANS tag accepte
    n'importe quel tag installé (c'est la sémantique d'`ollama pull`)."""
    if required in installed:
        return True
    if ":" not in required:
        return any(name.split(":", 1)[0] == required for name in installed)
    return False


def get_ollama_status() -> dict:
    """Distingue joignable / prêt pour Ollama (BYO, chantier 6).

    Une instance qui répond mais à qui il manque les modèles que le
    routage va demander est le même faux vert que ComfyUI : `ready`
    seulement si TOUS les modèles de génération configurés sont présents
    dans /api/tags. Le modèle d'EMBEDDING est à part : la couche
    embeddings du routeur se dégrade proprement quand il manque → absent
    = signalé dans reason, `ready` intact.

    Import engine tardif (même règle que les imports clients).
    """
    from app.infra.ollama_runtime import configured_generation_models, get_embed_model

    tags_url = get_ollama_tags_url()
    try:
        response = requests.get(tags_url, timeout=3.0)
    except requests.RequestException as exc:
        return {"reachable": False, "ready": False, "reason": str(exc), "missing": []}
    if not response.ok:
        return {
            "reachable": True,
            "ready": False,
            "reason": f"http {response.status_code}",
            "missing": [],
        }
    try:
        installed = {
            str(entry.get("name"))
            for entry in (response.json().get("models") or [])
            if isinstance(entry, dict) and entry.get("name")
        }
    except ValueError:
        return {
            "reachable": True,
            "ready": False,
            "reason": "joignable mais /api/tags illisible",
            "missing": [],
        }

    missing = sorted(
        model
        for model in configured_generation_models()
        if not _ollama_model_present(model, installed)
    )
    embed_model = get_embed_model()
    embed_note = (
        ""
        if _ollama_model_present(embed_model, installed)
        else (
            f"; embedding optionnel absent: {embed_model}"
            " (fallback semantique du routeur desactive)"
        )
    )
    if missing:
        return {
            "reachable": True,
            "ready": False,
            "reason": "joignable mais modeles requis manquants: "
            + ", ".join(missing)
            + embed_note,
            "missing": missing,
        }
    return {
        "reachable": True,
        "ready": True,
        "reason": f"joignable; {len(installed)} modeles installes; "
        "modeles requis presents" + embed_note,
        "missing": [],
    }


def get_comfyui_status() -> dict:
    """Distinguish reachable / ready / degraded for ComfyUI.

    An empty-but-reachable ComfyUI is the classic false green: it answers HTTP but the
    configured checkpoint/upscaler are absent, so a render fails with "... not in []".
    `ready` is True only when every configured model is present in /object_info.
    """
    reachable, reachable_reason = is_comfyui_ready()
    if not reachable:
        return {"reachable": False, "ready": False, "reason": reachable_reason, "missing": []}

    available = _comfyui_available_models()
    if available is None:
        return {
            "reachable": True,
            "ready": False,
            "reason": "joignable mais /object_info illisible",
            "missing": [],
        }

    required = _comfyui_required_models()
    missing = sorted(
        {
            name
            for bucket, names in required.items()
            for name in names
            if name not in available.get(bucket, set())
        }
    )
    if missing:
        return {
            "reachable": True,
            "ready": False,
            "reason": "joignable mais modeles requis manquants: " + ", ".join(missing),
            "missing": missing,
        }
    return {
        "reachable": True,
        "ready": True,
        "reason": "joignable; modeles requis presents",
        "missing": [],
    }


def _start_bat_process(bat_path: str) -> None:
    bat_file = Path(bat_path)
    if not bat_file.exists():
        raise ToolManagerError(f"Launcher not found: {bat_file}")

    if os.name == "nt":
        launch_cmd = ["cmd", "/c", str(bat_file)]
    else:
        launch_cmd = ["bash", str(bat_file)]

    subprocess.Popen(
        launch_cmd,
        cwd=str(bat_file.parent),
        shell=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _wait_until_ready(checker, timeout_s: int, poll_interval_s: float) -> tuple[bool, str]:
    deadline = time.time() + timeout_s
    last_reason = "not ready"

    while time.time() < deadline:
        ok, reason = checker()
        if ok:
            return True, reason
        last_reason = reason
        time.sleep(poll_interval_s)

    return False, last_reason


def ensure_comfyui_ready_with_autostart() -> dict:
    ok, reason = is_comfyui_ready()
    if ok:
        return {
            "tool": "comfyui",
            "ready": True,
            "action": "none",
            "reason": "already running",
        }

    if not get_comfyui_auto_start():
        return {
            "tool": "comfyui",
            "ready": False,
            "action": "none",
            "reason": "COMFYUI_AUTO_START disabled",
        }

    if not COMFYUI_BAT_PATH:
        return {
            "tool": "comfyui",
            "ready": False,
            "action": "none",
            "reason": "COMFYUI_BAT_PATH not configured",
        }

    try:
        _start_bat_process(COMFYUI_BAT_PATH)
    except Exception as exc:
        return {
            "tool": "comfyui",
            "ready": False,
            "action": "start_failed",
            "reason": str(exc),
        }

    ok, reason = _wait_until_ready(
        checker=is_comfyui_ready,
        timeout_s=get_comfyui_start_timeout(),
        poll_interval_s=COMFYUI_POLL_INTERVAL,
    )

    return {
        "tool": "comfyui",
        "ready": ok,
        "action": "started",
        "reason": reason,
    }


def ensure_tool_ready(tool_name: str | None) -> dict:
    if tool_name is None:
        return {
            "tool": None,
            "ready": True,
            "action": "none",
            "reason": "no external tool required",
        }

    if tool_name == "web":
        ok, reason = is_searxng_ready()
        return {
            "tool": "web",
            "ready": ok,
            "action": "healthcheck",
            "reason": reason,
        }

    if tool_name == "comfyui":
        return ensure_comfyui_ready_with_autostart()

    raise ToolManagerError(f"Unknown tool_name: {tool_name}")


def ensure_llm_backend_ready() -> dict:
    ok, reason = is_ollama_ready()
    return {
        "tool": "ollama",
        "ready": ok,
        "action": "healthcheck",
        "reason": reason,
    }
