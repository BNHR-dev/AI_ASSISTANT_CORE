from __future__ import annotations

from app.engine.planner_types import ExecutionState


VISIBLE_STEP_TYPES = {
    "llm_primary",
    "llm_secondary",
    "llm_synthesis",
    "tool_comfyui",
}


def assemble_final_output(state: ExecutionState) -> str:
    outputs = [
        result.output
        for result in state.step_results
        if (
            result.output
            and result.step_type in VISIBLE_STEP_TYPES
            and result.status == "success"
        )
    ]
    final_output = "\n\n---\n\n".join(outputs).strip()

    if not final_output:
        fallback_outputs = [
            result.output
            for result in state.step_results
            if result.output and result.status == "error"
        ]
        if fallback_outputs:
            final_output = fallback_outputs[0].strip()

    if not final_output:
        final_output = "Aucun résultat exploitable n'a été produit."

    state.final_output = final_output
    state.add_trace("assembler → final_output")
    return final_output