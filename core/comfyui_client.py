"""Legacy compatibility shim for root-level imports."""

from app.clients.comfyui_client import (
    build_visual_request_from_text,
    run_comfyui_workflow,
)

__all__ = [
    "build_visual_request_from_text",
    "run_comfyui_workflow",
]