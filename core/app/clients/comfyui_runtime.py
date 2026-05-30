from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import requests


COMFYUI_URL = os.getenv("COMFYUI_URL", "http://localhost:8188").rstrip("/")
COMFYUI_BAT_PATH = os.getenv("COMFYUI_BAT_PATH", "").strip()
COMFYUI_AUTO_START = os.getenv("COMFYUI_AUTO_START", "1").lower() not in {"0", "false", "no"}
COMFYUI_START_TIMEOUT = int(os.getenv("COMFYUI_START_TIMEOUT", "45"))


def ping_comfyui(timeout: int = 4) -> bool:
    try:
        response = requests.get(COMFYUI_URL, timeout=timeout)
        return response.ok
    except Exception:
        return False


def resolve_comfyui_bat_path() -> Path:
    if not COMFYUI_BAT_PATH:
        raise RuntimeError("COMFYUI_BAT_PATH not configured")

    path = Path(COMFYUI_BAT_PATH)
    if not path.exists():
        raise RuntimeError(f"COMFYUI_BAT_PATH not found: {path}")

    return path


def start_comfyui_process() -> subprocess.Popen:
    bat_path = resolve_comfyui_bat_path()

    kwargs = {
        "cwd": str(bat_path.parent),
    }

    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_CONSOLE  # type: ignore[attr-defined]
        launch_cmd = ["cmd", "/c", str(bat_path)]
    else:
        launch_cmd = ["bash", str(bat_path)]

    return subprocess.Popen(launch_cmd, **kwargs)


def wait_for_comfyui_ready(timeout_seconds: int | None = None) -> None:
    timeout = timeout_seconds or COMFYUI_START_TIMEOUT
    deadline = time.time() + timeout

    while time.time() < deadline:
        if ping_comfyui():
            return
        time.sleep(1)

    raise RuntimeError(
        f"Unable to reach ComfyUI at {COMFYUI_URL}. Verify that ComfyUI is running and its API is exposed."
    )


def ensure_comfyui_runtime() -> dict:
    if ping_comfyui():
        return {"ready": True, "started": False}

    if not COMFYUI_AUTO_START:
        raise RuntimeError(
            f"Unable to reach ComfyUI at {COMFYUI_URL}. Verify that ComfyUI is running and its API is exposed."
        )

    process = start_comfyui_process()
    wait_for_comfyui_ready()

    return {
        "ready": True,
        "started": True,
        "pid": getattr(process, "pid", None),
    }
