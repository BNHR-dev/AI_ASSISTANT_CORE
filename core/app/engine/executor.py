from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from app.engine.blender_script_quality import analyze_blender_script_quality
from app.engine.planner_service import build_execution_plan
from app.engine.planner_types import StepResult
from app.engine.result_assembler import assemble_final_output
from app.engine.router_service import build_route_decision
from app.engine.run_events import emit_run_event
from app.engine.run_state import (
    load_run_state,
    rebuild_plan,
    rebuild_step_result,
    save_run_state,
)
from app.engine.routing_conditions import enrich_route_config
from app.engine.execution_state_factory import create_execution_state
from app.engine.step_executor import execute_step
from app.engine.task_routing import TASK_ROUTING
from app.tool_selector import select_tool


FORCED_MODE_TO_TASK_TYPE = {
    "explain": "explain_basic",
    "build": "build",
    "architecture": "architecture",
    "critique": "critique",
    "vision": "vision",
    "image_generation": "image_generation",
    "blender_script": "blender_script",
    "web_research": "web_research",
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
    awaiting_step_ids = [
        result.step_id
        for result in state.step_results
        if result.status == "awaiting_user"
    ]

    # 4B — un run en attente d'approbation n'est ni un échec ni un succès :
    # "paused" (la reprise vaut approbation). Une erreur déjà survenue prime.
    if awaiting_step_ids and not error_step_ids:
        status = "paused"
    elif error_step_ids or blocked_step_ids:
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
        "awaiting_step_ids": awaiting_step_ids,
    }


def _load_manifest(manifest_path: str | None) -> dict | None:
    """Charge manifest.json depuis le disque. Retourne None si absent ou illisible."""
    if not manifest_path:
        return None
    try:
        return json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def _extract_blender_artifact(state) -> dict:
    for result in reversed(state.step_results):
        if result.step_type != "tool_blender":
            continue

        meta = result.meta if isinstance(result.meta, dict) else {}
        blender_status = meta.get("status") or result.status
        output_path = meta.get("output_path")

        # artifact_type/path/filename seulement si success et fichier produit
        if blender_status == "success" and output_path:
            artifact_type = "blend"
            artifact_path = output_path
            artifact_filename = "scene.blend"
        else:
            artifact_type = None
            artifact_path = None
            artifact_filename = None

        return {
            "artifact_type": artifact_type,
            "artifact_path": artifact_path,
            "artifact_filename": artifact_filename,
            "blender_status": blender_status,
            "blender_script_path": meta.get("script_path"),
            "blender_output_path": output_path,
            "blender_returncode": meta.get("returncode"),
            "blender_stdout": meta.get("stdout"),
            "blender_stderr": meta.get("stderr"),
            "blender_error": meta.get("error"),
            "blender_render_path": meta.get("render_path"),
            "blender_scene_report": meta.get("scene_report"),
            "blender_scene_report_path": meta.get("scene_report_path"),
            "blender_manifest_path": meta.get("manifest_path"),
            "blender_manifest": _load_manifest(meta.get("manifest_path")),
        }

    return {}


def _extract_visual_artifact(state) -> dict:
    for result in reversed(state.step_results):
        if result.step_type != "tool_comfyui":
            continue

        meta = result.meta if isinstance(result.meta, dict) else {}
        artifact_path = meta.get("output_path")
        artifact_filename = meta.get("filename")
        artifact_paths = meta.get("output_paths") or ([] if artifact_path is None else [artifact_path])
        artifact_filenames = meta.get("filenames") or ([] if artifact_filename is None else [artifact_filename])
        artifact_view_url = meta.get("artifact_view_url")
        artifact_view_urls = meta.get("artifact_view_urls") or (
            [] if artifact_view_url is None else [artifact_view_url]
        )
        workflow_id = meta.get("workflow_id")
        comfyui_status = meta.get("status") or result.status

        return {
            "artifact_type": "image",
            "artifact_path": artifact_path,
            "artifact_filename": artifact_filename,
            "artifact_paths": artifact_paths,
            "artifact_filenames": artifact_filenames,
            "artifact_view_url": artifact_view_url,
            "artifact_view_urls": artifact_view_urls,
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


def _execute_one_step(state, step, request_id: str) -> None:
    """Un step : garde de dépendances, exécution, timings, traces, événements."""
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
        emit_run_event(
            request_id=request_id,
            kind="step.blocked",
            data={
                "step_id": step.step_id,
                "step_type": step.step_type,
                "error": blocked_result.error,
            },
        )
        return

    state.add_trace(f"step_executor → start:{step.step_id}")
    emit_run_event(
        request_id=request_id,
        kind="step.started",
        data={"step_id": step.step_id, "step_type": step.step_type},
    )

    # 4B — retry déclaré sur le step : ré-exécution bornée sur "error"
    # (défaut max_attempts=1 = aucun retry). Seul le résultat FINAL entre
    # dans step_results ; les tentatives intermédiaires vivent dans la
    # trace et le journal d'événements (step.retry).
    max_attempts = max(1, getattr(step, "max_attempts", 1))
    attempt = 1
    result = execute_step(state, step)
    while result.status == "error" and attempt < max_attempts:
        state.add_trace(
            f"step_executor → {step.step_id}:error attempt {attempt}/{max_attempts} → retry"
        )
        emit_run_event(
            request_id=request_id,
            kind="step.retry",
            data={
                "step_id": step.step_id,
                "attempt": attempt,
                "max_attempts": max_attempts,
                "error": result.error[:2000] if result.error else None,
            },
        )
        attempt += 1
        result = execute_step(state, step)

    result.started_at = result.started_at or step_started_at
    result.finished_at = result.finished_at or _utc_now_iso()
    result.duration_ms = (
        result.duration_ms
        if result.duration_ms is not None
        else max(0, int((perf_counter() - step_started_perf) * 1000))
    )
    if attempt > 1:
        result.meta = {**(result.meta or {}), "attempts": attempt}
    step.status = result.status
    state.add_result(result)
    state.add_trace(f"step_executor → {step.step_id}:{result.status}")
    emit_run_event(
        request_id=request_id,
        kind="step.finished",
        data={
            "step_id": step.step_id,
            "step_type": step.step_type,
            "status": result.status,
            "duration_ms": result.duration_ms,
            "attempts": attempt,
            # Événements légers : erreur tronquée, la version complète
            # reste dans step_results (réponse API) et les manifests.
            "error": result.error[:2000] if result.error else None,
        },
    )


def _run_pending_steps(
    state, plan, request_id: str, *, message: str, has_image: bool, mode: str, decision: dict
) -> None:
    """Exécute les steps non encore réussis, avec CHECKPOINT après chacun :
    un run interrompu reprend là où il s'est arrêté (4A). Un step marqué
    requires_approval ARRÊTE le run avant son exécution (4B) : status
    awaiting_user, checkpoint, et la reprise (resume_request) vaut
    approbation."""
    for step in plan.steps:
        if step.status == "success":
            continue

        if step.requires_approval:
            step.status = "awaiting_user"
            awaiting = StepResult(
                step_id=step.step_id,
                step_type=step.step_type,
                status="awaiting_user",
                error=None,
                started_at=_utc_now_iso(),
            )
            state.add_result(awaiting)
            state.add_trace(f"step_executor → {step.step_id}:awaiting_user")
            emit_run_event(
                request_id=request_id,
                kind="step.awaiting_user",
                data={"step_id": step.step_id, "step_type": step.step_type},
            )
            save_run_state(
                request_id,
                message=message,
                has_image=has_image,
                mode=mode,
                decision=decision,
                plan=plan,
                step_results=state.step_results,
            )
            break  # rien après le step en attente : le run est en pause

        _execute_one_step(state, step, request_id)
        save_run_state(
            request_id,
            message=message,
            has_image=has_image,
            mode=mode,
            decision=decision,
            plan=plan,
            step_results=state.step_results,
        )


def execute_request(
    message: str,
    has_image: bool = False,
    mode: str = "auto",
    pause_before_tools: bool = False,
) -> dict:
    request_id = str(uuid4())
    started_at = _utc_now_iso()
    started_perf = perf_counter()

    emit_run_event(
        request_id=request_id,
        kind="run.started",
        data={
            "message": message,
            "mode": mode,
            "has_image": has_image,
            "pause_before_tools": pause_before_tools,
            "started_at": started_at,
        },
    )

    if mode == "auto":
        decision = build_route_decision(message, has_image)
    else:
        decision = _build_forced_mode_decision(message, mode)

    print("=== DECISION TRACE ===")
    for item in decision.get("decision_trace", []):
        print(item)
    print("======================")

    emit_run_event(
        request_id=request_id,
        kind="route.decided",
        data={
            "task_type": decision.get("task_type"),
            "primary_agent": decision.get("primary_agent"),
            "selected_model": decision.get("selected_model"),
            "selected_tool": decision.get("selected_tool"),
            "needs_web": decision.get("needs_web"),
            "second_call": decision.get("second_call"),
            "matched_rule": decision.get("matched_rule"),
            "decision_trace": decision.get("decision_trace"),
        },
    )

    plan = build_execution_plan(decision, message)
    # 4B — pause demandée par l'appelant : chaque step OUTIL exige une
    # approbation avant exécution (inspection du plan/des steps amont, puis
    # resume). Opt-in par requête : aucun changement sans le flag.
    if pause_before_tools:
        for step in plan.steps:
            if step.step_type.startswith("tool_"):
                step.requires_approval = True
    state = create_execution_state(message, decision, plan)
    # Le pipeline Blender (prepare_blender_script) lit ce request_id pour nommer
    # outputs/blender/<request_id>/ — sans cette propagation, il génère un uuid
    # distinct et la corrélation API ↔ artefacts est cassée (audit 2026-06-10, A1).
    state.context["request_id"] = request_id
    state.add_trace(f"request_id → {request_id}")
    state.add_trace(f"executor → started_at:{started_at}")

    emit_run_event(
        request_id=request_id,
        kind="plan.built",
        data={
            "strategy": plan.strategy,
            "steps": [
                {
                    "step_id": step.step_id,
                    "step_type": step.step_type,
                    "agent": step.agent,
                    "model": step.model,
                    "tool": step.tool,
                    "depends_on": step.depends_on,
                }
                for step in plan.steps
            ],
        },
    )

    _run_pending_steps(
        state, plan, request_id,
        message=message, has_image=has_image, mode=mode, decision=decision,
    )
    return _finalize_run(
        state, plan, decision, request_id, started_at, started_perf,
        message=message, has_image=has_image, mode=mode,
    )


def resume_request(request_id: str) -> dict:
    """
    Reprend un run interrompu depuis son checkpoint (state.json) : les steps
    déjà RÉUSSIS sont restaurés tels quels (leurs sorties redeviennent
    disponibles pour les steps dépendants), tout le reste est ré-exécuté.

    LookupError si aucun checkpoint exploitable n'existe pour ce request_id.
    """
    saved = load_run_state(request_id)
    if saved is None:
        raise LookupError(f"no saved state for request_id {request_id}")
    try:
        plan = rebuild_plan(saved["plan"])
        restored = [
            rebuild_step_result(raw)
            for raw in saved.get("step_results") or []
            if isinstance(raw, dict) and raw.get("status") == "success"
        ]
    except (TypeError, KeyError) as exc:
        raise LookupError(f"unusable saved state for request_id {request_id}: {exc}") from exc

    message = saved["message"]
    has_image = bool(saved.get("has_image"))
    mode = saved.get("mode") or "auto"
    decision = saved["decision"]

    started_at = _utc_now_iso()
    started_perf = perf_counter()

    state = create_execution_state(message, decision, plan)
    state.context["request_id"] = request_id

    # Seuls les succès sont restaurés ; les steps error/blocked/awaiting
    # repartent de zéro (statut pending), leurs anciens résultats ne sont
    # pas rejoués. 4B : la reprise VAUT approbation — les gates
    # requires_approval sont levées pour toute la continuation (sinon le
    # run se remettrait en pause au même step, indéfiniment).
    restored_ids = {result.step_id for result in restored}
    for step in plan.steps:
        step.status = "success" if step.step_id in restored_ids else "pending"
        step.requires_approval = False
    for result in restored:
        state.add_result(result)

    state.add_trace(f"request_id → {request_id}")
    state.add_trace(
        f"executor → resumed_at:{started_at} restored:{sorted(restored_ids)}"
    )
    emit_run_event(
        request_id=request_id,
        kind="run.resumed",
        data={
            "restored_step_ids": sorted(restored_ids),
            "pending_step_ids": [
                step.step_id for step in plan.steps if step.status != "success"
            ],
            "resumed_at": started_at,
        },
    )

    _run_pending_steps(
        state, plan, request_id,
        message=message, has_image=has_image, mode=mode, decision=decision,
    )
    return _finalize_run(
        state, plan, decision, request_id, started_at, started_perf,
        message=message, has_image=has_image, mode=mode,
    )


def _finalize_run(
    state, plan, decision: dict, request_id: str, started_at: str, started_perf: float,
    *, message: str, has_image: bool, mode: str,
) -> dict:
    final_output = assemble_final_output(state)
    execution_summary = _build_execution_summary(plan, state)
    finished_at = _utc_now_iso()
    duration_ms = max(0, int((perf_counter() - started_perf) * 1000))
    state.add_trace(f"executor → finished_at:{finished_at}")
    state.add_trace(f"executor → duration_ms:{duration_ms}")

    emit_run_event(
        request_id=request_id,
        kind="run.finished",
        data={
            "execution_summary": execution_summary,
            "finished_at": finished_at,
            "duration_ms": duration_ms,
        },
    )

    # Checkpoint final avec le statut du run : un run degraded/failed reste
    # repris (resume_request), un run success garde sa photo pour audit.
    save_run_state(
        request_id,
        message=message,
        has_image=has_image,
        mode=mode,
        decision=decision,
        plan=plan,
        step_results=state.step_results,
        run_status=execution_summary["status"],
    )

    visual_artifact = _extract_visual_artifact(state)
    blender_artifact = _extract_blender_artifact(state)

    _raw_quality = analyze_blender_script_quality(message, final_output)
    blender_quality_report = (
        {
            "is_blender": True,
            "violations": _raw_quality["violations"],
            "passed": len(_raw_quality["violations"]) == 0,
        }
        if _raw_quality["is_blender"]
        else None
    )

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
        "blender_quality_report": blender_quality_report,
        **visual_artifact,
        **blender_artifact,
    }