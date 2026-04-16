from __future__ import annotations

from datetime import datetime, timezone
from time import perf_counter
from uuid import uuid4

from app.engine.planner_service import build_execution_plan
from app.engine.planner_types import StepResult
from app.engine.result_assembler import assemble_final_output
from app.engine.router_service import build_route_decision
from app.engine.routing_conditions import enrich_route_config
from app.engine.state_store import create_execution_state
from app.engine.step_executor import execute_step
from app.engine.task_routing import TASK_ROUTING
from app.tool_selector import select_tool


FORCED_MODE_TO_TASK_TYPE = {
    "explain": "explain_basic",
    "build": "build",
    "architecture": "architecture",
    "critique": "critique",
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _build_execution_summary(plan, state) -> dict:
    successful_step_ids = [
        result.step_id for result in state.step_results if result.status == "success"
    ]
    error_step_ids = [
        result.step_id for result in state.step_results if result.status == "error"
    ]
    blocked_step_ids = [
        result.step_id for result in state.step_results if result.status == "blocked"
    ]

    if error_step_ids or blocked_step_ids:
        status = "degraded" if successful_step_ids else "failed"
    elif successful_step_ids:
        status = "success"
    else:
        status = "empty"

    return {
        "status": status,
        "total_steps": len(plan.steps),
        "successful_step_ids": successful_step_ids,
        "error_step_ids": error_step_ids,
        "blocked_step_ids": blocked_step_ids,
    }


def _extract_visual_artifact(state) -> dict:
    for result in reversed(state.step_results):
        if result.step_type != "tool_comfyui":
            continue

        meta = result.meta if isinstance(result.meta, dict) else {}
        artifact_path = meta.get("output_path")
        artifact_filename = meta.get("filename")
        artifact_paths = meta.get("output_paths") or ([] if artifact_path is None else [artifact_path])
        artifact_filenames = meta.get("filenames") or ([] if artifact_filename is None else [artifact_filename])
        workflow_id = meta.get("workflow_id")
        comfyui_status = meta.get("status") or result.status

        return {
            "artifact_type": "image",
            "artifact_path": artifact_path,
            "artifact_filename": artifact_filename,
            "artifact_paths": artifact_paths,
            "artifact_filenames": artifact_filenames,
            "workflow_id": workflow_id,
            "comfyui_status": comfyui_status,
            "comfyui_prompt_id": meta.get("prompt_id"),
            "variants_count": meta.get("variants_count"),
            "completed_variants": meta.get("completed_variants"),
            "partial_visual_success": meta.get("partial"),
        }

    return {}


def _build_forced_mode_decision(message: str, mode: str) -> dict:
    forced_task_type = FORCED_MODE_TO_TASK_TYPE.get(mode)
    if forced_task_type is None:
        raise ValueError(f"Unknown forced mode: {mode}")

    base_route = TASK_ROUTING.get(forced_task_type)
    if base_route is None:
        raise ValueError(f"Unknown forced task_type: {forced_task_type}")

    base_config = {
        "task_type": base_route.task_type,
        "primary_agent": base_route.primary_agent,
        "selected_model": base_route.model,
        "needs_web": base_route.web,
        "second_call": base_route.second_call,
        "output_format": base_route.output_format,
    }

    enriched_config = enrich_route_config(
        task_type=forced_task_type,
        user_text=message,
        base_config=base_config,
    )

    decision_trace = [
        f"forced_mode → {mode}",
        f"forced_task → {forced_task_type}",
        f"final_task → {enriched_config['task_type']}",
    ]

    if enriched_config.get("matched_rule"):
        decision_trace.append(f"rule → {enriched_config['matched_rule']}")

    selected_tool = select_tool(message, enriched_config["task_type"])
    if selected_tool:
        decision_trace.append(f"tool_suggestion → {selected_tool}")

    if enriched_config["task_type"] == "image_generation":
        selected_tool = "comfyui"
        decision_trace.append("forced_tool → comfyui")

    decision_trace.append(f"final_tool → {selected_tool}")

    classifier_reason = f"Mode forcé: {mode}"
    rule_reason = enriched_config.get("reason_debug")
    reason_debug = classifier_reason

    if rule_reason:
        reason_debug = f"{classifier_reason} | {rule_reason}"

    final_decision = {
        **enriched_config,
        "classifier_reason": classifier_reason,
        "reason_debug": reason_debug,
        "selected_tool": selected_tool,
        "decision_trace": decision_trace,
        "decision_path": decision_trace.copy(),
    }

    reason_parts = [
        classifier_reason,
        f"Agent : {final_decision['primary_agent']}",
        f"Modèle : {final_decision['selected_model']}",
        f"Web : {'oui' if final_decision['needs_web'] else 'non'}",
    ]

    if final_decision.get("second_call"):
        reason_parts.append(f"Second call : {final_decision['second_call']}")

    if selected_tool:
        reason_parts.append(f"Tool : {selected_tool}")

    if final_decision.get("matched_rule"):
        reason_parts.append(f"Règle : {final_decision['matched_rule']}")

    final_decision["reason"] = " | ".join(reason_parts)
    return final_decision


def execute_request(message: str, has_image: bool = False, mode: str = "auto") -> dict:
    request_id = str(uuid4())
    started_at = _utc_now_iso()
    started_perf = perf_counter()

    if mode == "auto":
        decision = build_route_decision(message, has_image)
    else:
        decision = _build_forced_mode_decision(message, mode)

    print("=== DECISION TRACE ===")
    for item in decision.get("decision_trace", []):
        print(item)
    print("======================")

    plan = build_execution_plan(decision, message)
    state = create_execution_state(message, decision, plan)
    state.add_trace(f"request_id → {request_id}")
    state.add_trace(f"executor → started_at:{started_at}")

    for step in plan.steps:
        step_started_at = _utc_now_iso()
        step_started_perf = perf_counter()

        unmet_dependencies = [
            dep
            for dep in step.depends_on
            if not any(
                result.step_id == dep and result.status == "success"
                for result in state.step_results
            )
        ]

        if unmet_dependencies:
            blocked_finished_at = _utc_now_iso()
            blocked_duration_ms = max(
                0,
                int((perf_counter() - step_started_perf) * 1000),
            )
            blocked_result = StepResult(
                step_id=step.step_id,
                step_type=step.step_type,
                status="blocked",
                error=f"Blocked by unmet dependencies: {', '.join(unmet_dependencies)}",
                started_at=step_started_at,
                finished_at=blocked_finished_at,
                duration_ms=blocked_duration_ms,
            )
            step.status = blocked_result.status
            state.add_result(blocked_result)
            state.add_trace(f"step_executor → {step.step_id}:blocked")
            continue

        state.add_trace(f"step_executor → start:{step.step_id}")
        result = execute_step(state, step)
        result.started_at = result.started_at or step_started_at
        result.finished_at = result.finished_at or _utc_now_iso()
        result.duration_ms = (
            result.duration_ms
            if result.duration_ms is not None
            else max(0, int((perf_counter() - step_started_perf) * 1000))
        )
        step.status = result.status
        state.add_result(result)
        state.add_trace(f"step_executor → {step.step_id}:{result.status}")

    final_output = assemble_final_output(state)
    execution_summary = _build_execution_summary(plan, state)
    finished_at = _utc_now_iso()
    duration_ms = max(0, int((perf_counter() - started_perf) * 1000))
    state.add_trace(f"executor → finished_at:{finished_at}")
    state.add_trace(f"executor → duration_ms:{duration_ms}")

    visual_artifact = _extract_visual_artifact(state)

    return {
        **decision,
        "execution_strategy": plan.strategy,
        "execution_summary": execution_summary,
        "request_id": request_id,
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_ms": duration_ms,
        "plan": [
            {
                "step_id": step.step_id,
                "step_type": step.step_type,
                "goal": step.goal,
                "agent": step.agent,
                "model": step.model,
                "tool": step.tool,
                "depends_on": step.depends_on,
                "status": step.status,
            }
            for step in plan.steps
        ],
        "step_results": [
            {
                "step_id": result.step_id,
                "step_type": result.step_type,
                "status": result.status,
                "output": result.output,
                "error": result.error,
                "meta": result.meta,
                "started_at": result.started_at,
                "finished_at": result.finished_at,
                "duration_ms": result.duration_ms,
            }
            for result in state.step_results
        ],
        "decision_trace": state.trace,
        "primary_output": state.get_output("step_primary"),
        "second_output": state.get_output("step_secondary"),
        "output": final_output,
        **visual_artifact,
    }