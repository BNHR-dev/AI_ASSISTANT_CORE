"""Legacy compatibility shim for root-level imports."""

from app.task_classifier import classify_task, classify_task_v2

__all__ = ["classify_task", "classify_task_v2"]
