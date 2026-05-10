from typing import Any, Optional

from pydantic import BaseModel, Field


class RouteRequest(BaseModel):
    message: str
    has_image: bool = False


class PlanStepResponse(BaseModel):
    step_id: str
    step_type: str
    goal: str
    agent: Optional[str] = None
    model: Optional[str] = None
    tool: Optional[str] = None
    depends_on: list[str] = Field(default_factory=list)
    status: Optional[str] = None


class StepResultResponse(BaseModel):
    step_id: str
    step_type: str
    status: str
    output: Optional[Any] = None
    error: Optional[str] = None
    meta: dict[str, Any] = Field(default_factory=dict)
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    duration_ms: Optional[int] = None


class ExecutionSummaryResponse(BaseModel):
    status: str
    total_steps: int
    successful_step_ids: list[str] = Field(default_factory=list)
    error_step_ids: list[str] = Field(default_factory=list)
    blocked_step_ids: list[str] = Field(default_factory=list)


class ToolStatusResponse(BaseModel):
    name: str
    ready: bool
    required: bool
    role: str
    reason: str
    endpoint: Optional[str] = None
    activity: Optional[str] = None


class RuntimeHealthResponse(BaseModel):
    status: str
    version: str
    checked_at: str
    summary: str
    services: dict[str, ToolStatusResponse] = Field(default_factory=dict)


class CanonicalBoundariesResponse(BaseModel):
    status: str
    version: str
    canonical_paths: list[str] = Field(default_factory=list)
    legacy_shims: list[str] = Field(default_factory=list)
    active_runtime_modules: list[str] = Field(default_factory=list)
    active_auxiliary_modules: list[str] = Field(default_factory=list)
    optional_runtime_services: list[str] = Field(default_factory=list)
    dormant_modules: list[str] = Field(default_factory=list)
    rule: str


class RouteResponse(BaseModel):
    task_type: str
    primary_agent: str
    selected_model: str
    needs_web: bool
    second_call: Optional[str] = None
    output_format: str
    selected_tool: Optional[str] = None
    matched_rule: Optional[str] = None
    reason_debug: Optional[str] = None
    classifier_reason: Optional[str] = None
    decision_trace: list[str] = Field(default_factory=list)
    decision_path: list[str] = Field(default_factory=list)
    reason: str


class ExecuteRequest(BaseModel):
    message: str
    has_image: bool = False


class ExecuteResponse(BaseModel):
    task_type: str
    primary_agent: str
    selected_model: str
    needs_web: bool
    second_call: Optional[str] = None
    output_format: str
    selected_tool: Optional[str] = None
    matched_rule: Optional[str] = None
    reason_debug: Optional[str] = None
    classifier_reason: Optional[str] = None
    decision_trace: list[str] = Field(default_factory=list)
    decision_path: list[str] = Field(default_factory=list)
    reason: str
    execution_strategy: Optional[str] = None
    execution_summary: Optional[ExecutionSummaryResponse] = None
    request_id: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    duration_ms: Optional[int] = None
    plan: list[PlanStepResponse] = Field(default_factory=list)
    step_results: list[StepResultResponse] = Field(default_factory=list)
    primary_output: Optional[str] = None
    second_output: Optional[str] = None
    artifact_type: Optional[str] = None
    artifact_path: Optional[str] = None
    artifact_filename: Optional[str] = None
    artifact_paths: list[str] = Field(default_factory=list)
    artifact_filenames: list[str] = Field(default_factory=list)
    workflow_id: Optional[str] = None
    comfyui_status: Optional[str] = None
    comfyui_prompt_id: Optional[str] = None
    variants_count: Optional[int] = None
    completed_variants: Optional[int] = None
    partial_visual_success: Optional[bool] = None
    blender_quality_report: Optional[dict[str, Any]] = None
    blender_status: Optional[str] = None
    blender_script_path: Optional[str] = None
    blender_output_path: Optional[str] = None
    blender_returncode: Optional[int] = None
    blender_stdout: Optional[str] = None
    blender_stderr: Optional[str] = None
    blender_error: Optional[str] = None
    blender_render_path: Optional[str] = None
    output: str
