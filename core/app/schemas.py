from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from app.engine.run_identity import REQUEST_ID_PATTERN


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
    # 4B — steps en attente d'approbation (status global "paused") ;
    # la reprise via POST /resume vaut approbation.
    awaiting_step_ids: list[str] = Field(default_factory=list)


class ToolStatusResponse(BaseModel):
    name: str
    ready: bool
    required: bool
    role: str
    reason: str
    endpoint: Optional[str] = None
    activity: Optional[str] = None
    # reachable distinguishes "service answers" from "service is READY to serve". A
    # ComfyUI that answers HTTP but is missing the configured models is reachable=True,
    # ready=False (no false green). `missing` lists the absent required model names.
    reachable: Optional[bool] = None
    missing: list[str] = Field(default_factory=list)


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


class ReproduceRequest(BaseModel):
    """Rejeu d'un run depuis son manifest v2.

    Le client (CLI) lit les fichiers du run sur SON disque et transmet leur
    CONTENU — le backend ne résout jamais de chemins client (les chemins d'un
    manifest Docker n'existent pas côté hôte, et inversement).
    """
    pipeline: Literal["comfyui", "blender"]
    manifest: dict[str, Any]
    # ComfyUI : index de variante (clé JSON = str) → contenu du sidecar
    # workflow_resolved_v<i>.json.
    workflows: dict[str, dict[str, Any]] = Field(default_factory=dict)
    # Blender : contenu du scene.py du run.
    scene_py: Optional[str] = None


class ReproduceResponse(BaseModel):
    pipeline: str
    verdict: str
    dhash_threshold: int
    reproduced_request_id: Optional[str] = None
    created_at: Optional[str] = None
    variants: list[dict[str, Any]] = Field(default_factory=list)
    checks: list[dict[str, Any]] = Field(default_factory=list)
    environment_diffs: list[dict[str, Any]] = Field(default_factory=list)
    error: Optional[str] = None
    report_path: Optional[str] = None
    duration_ms: Optional[int] = None


class ResumeRequest(BaseModel):
    """Reprise d'un run interrompu depuis son checkpoint (state.json) :
    les steps déjà réussis sont restaurés, le reste est ré-exécuté."""
    # Le contrat canonique (run_identity) est appliqué dès la frontière API :
    # request_id nomme un dossier sous outputs/runs/, jamais un chemin libre.
    request_id: str = Field(pattern=REQUEST_ID_PATTERN)


class ExecuteRequest(BaseModel):
    message: str
    has_image: bool = False
    # 4B — human-in-the-loop opt-in : le run s'arrête AVANT chaque step
    # outil (status "paused"), la reprise via POST /resume vaut approbation.
    pause_before_tools: bool = False


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
    blender_scene_report: Optional[dict[str, Any]] = None
    blender_scene_report_path: Optional[str] = None
    blender_manifest_path: Optional[str] = None
    blender_manifest: Optional[Any] = None
    output: str
