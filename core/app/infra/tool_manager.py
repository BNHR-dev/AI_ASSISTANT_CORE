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
    return _http_ok(get_comfyui_url())


def _start_bat_process(bat_path: str) -> None:
    bat_file = Path(bat_path)
    if not bat_file.exists():
        raise ToolManagerError(f"Launcher not found: {bat_file}")

    subprocess.Popen(
        ["cmd", "/c", str(bat_file)],
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
