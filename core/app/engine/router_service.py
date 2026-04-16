from app.engine.routing_conditions import enrich_route_config
from app.engine.task_routing import TASK_ROUTING
from app.task_classifier import classify_task
from app.tool_selector import select_tool


def build_route_decision(message: str, has_image: bool = False) -> dict:
    decision_trace = []

    task_type, task_reason = classify_task(message, has_image)
    decision_trace.append(f"classifier → {task_type}")

    route = TASK_ROUTING.get(task_type)
    if route is None:
        route = TASK_ROUTING["explain_basic"]
        task_type = "explain_basic"
        decision_trace.append("fallback → explain_basic")

    base_config = {
        "task_type": route.task_type,
        "primary_agent": route.primary_agent,
        "selected_model": route.model,
        "needs_web": route.web,
        "second_call": route.second_call,
        "output_format": route.output_format,
    }

    enriched_config = enrich_route_config(
        task_type=task_type,
        user_text=message,
        base_config=base_config,
    )

    decision_trace.append(f"final_task → {enriched_config['task_type']}")

    if enriched_config.get("matched_rule"):
        decision_trace.append(f"rule → {enriched_config['matched_rule']}")

    selected_tool = select_tool(message, enriched_config["task_type"])
    if selected_tool:
        decision_trace.append(f"tool_suggestion → {selected_tool}")

    if enriched_config["task_type"] == "image_generation":
        selected_tool = "comfyui"
        decision_trace.append("forced_tool → comfyui")

    decision_trace.append(f"final_tool → {selected_tool}")

    final_decision = {
        **enriched_config,
        "classifier_reason": task_reason,
        "reason_debug": task_reason,
        "selected_tool": selected_tool,
        "decision_trace": decision_trace,
        "decision_path": decision_trace.copy(),
    }

    reason_parts = [
        task_reason,
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