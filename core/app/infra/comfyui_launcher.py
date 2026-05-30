from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import requests


COMFYUI_URL = os.getenv("COMFYUI_URL", "http://localhost:8188")
COMFYUI_START_TIMEOUT = int(os.getenv("COMFYUI_START_TIMEOUT", "90"))
COMFYUI_POLL_INTERVAL = float(os.getenv("COMFYUI_POLL_INTERVAL", "2"))
COMFYUI_BAT_PATH = os.getenv("COMFYUI_BAT_PATH", "")


def is_comfyui_running() -> bool:
    try:
        response = requests.get(COMFYUI_URL, timeout=2)
        return response.status_code < 500
    except requests.RequestException:
        return False


def start_comfyui_process(bat_path: str | None = None) -> None:
    final_bat = bat_path or COMFYUI_BAT_PATH
    if not final_bat:
        raise RuntimeError(
            "COMFYUI_BAT_PATH is not configured. "
            "Set COMFYUI_BAT_PATH to your run_nvidia_gpu.bat absolute path."
        )

    bat_file = Path(final_bat)
    if not bat_file.exists():
        raise RuntimeError(f"ComfyUI launcher not found: {bat_file}")

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


def wait_for_comfyui_ready(timeout_s: int | None = None) -> bool:
    timeout = timeout_s or COMFYUI_START_TIMEOUT
    deadline = time.time() + timeout

    while time.time() < deadline:
        if is_comfyui_running():
            return True
        time.sleep(COMFYUI_POLL_INTERVAL)

    return False


def ensure_comfyui_ready(bat_path: str | None = None, retry: int = 1) -> tuple[bool, str]:
    if is_comfyui_running():
        return True, "ComfyUI already running"

    last_reason = "ComfyUI not started"

    for attempt in range(retry + 1):
        try:
            start_comfyui_process(bat_path=bat_path)
        except Exception as exc:
            return False, f"Unable to start ComfyUI: {exc}"

        if wait_for_comfyui_ready():
            return True, f"ComfyUI started successfully (attempt {attempt + 1})"

        last_reason = f"ComfyUI startup timed out (attempt {attempt + 1})"

    return False, last_reason
