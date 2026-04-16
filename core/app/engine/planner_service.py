from __future__ import annotations

from app.engine.plan_builder import build_plan_from_decision
from app.engine.planner_types import ExecutionPlan


def build_execution_plan(decision: dict, message: str) -> ExecutionPlan:
    return build_plan_from_decision(decision, message)
