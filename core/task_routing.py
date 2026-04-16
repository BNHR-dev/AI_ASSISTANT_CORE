"""Legacy compatibility shim for root-level imports."""

from app.engine.task_routing import TASK_ROUTING, TaskRoute

__all__ = ["TASK_ROUTING", "TaskRoute"]