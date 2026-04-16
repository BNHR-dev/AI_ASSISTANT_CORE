from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PlanStep:
    step_id: str
    step_type: str
    goal: str
    agent: str | None = None
    model: str | None = None
    tool: str | None = None
    output_format: str | None = None
    depends_on: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)
    status: str = "pending"


@dataclass
class ExecutionPlan:
    task_type: str
    steps: list[PlanStep]
    strategy: str = "single_step"


@dataclass
class StepResult:
    step_id: str
    step_type: str
    status: str
    output: str | None = None
    error: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    started_at: str | None = None
    finished_at: str | None = None
    duration_ms: int | None = None


@dataclass
class ExecutionState:
    message: str
    decision: dict[str, Any]
    plan: ExecutionPlan
    step_results: list[StepResult] = field(default_factory=list)
    trace: list[str] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)
    final_output: str | None = None

    def add_trace(self, item: str) -> None:
        self.trace.append(item)

    def add_result(self, result: StepResult) -> None:
        self.step_results.append(result)

    def get_output(self, step_id: str) -> str | None:
        for result in self.step_results:
            if result.step_id == step_id:
                return result.output
        return None
