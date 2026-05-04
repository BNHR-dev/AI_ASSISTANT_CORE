import pytest

from app.engine.planner_types import ExecutionPlan, ExecutionState, StepResult
from app.engine.result_assembler import VISIBLE_STEP_TYPES, assemble_final_output

# Internal metadata keys that must never surface in the final visible output.
_INTERNAL_META_KEYS = {
    "subject_type",
    "render_intent",
    "style_flags",
    "workflow_reason",
    "parameters",
}


def _state_with_steps(*step_results: StepResult) -> ExecutionState:
    return ExecutionState(
        message="test",
        decision={},
        plan=ExecutionPlan(task_type="image_generation", steps=[]),
        step_results=list(step_results),
    )


# --- Structural contract ---

def test_prepare_visual_is_not_a_visible_step_type():
    assert "prepare_visual" not in VISIBLE_STEP_TYPES


def test_visible_step_types_are_the_expected_set():
    assert VISIBLE_STEP_TYPES == {
        "llm_primary",
        "llm_secondary",
        "llm_synthesis",
        "tool_comfyui",
    }


# --- prepare_visual output excluded from final response ---

def test_prepare_visual_output_not_included_in_final():
    state = _state_with_steps(
        StepResult(
            step_id="prepare",
            step_type="prepare_visual",
            status="success",
            output="workflow=object_basic_v1 | subject=product | intent=packshot",
        ),
        StepResult(
            step_id="comfy",
            step_type="tool_comfyui",
            status="success",
            output="IMAGE_GENERATED",
        ),
    )

    output = assemble_final_output(state)

    assert output == "IMAGE_GENERATED"
    assert "workflow=" not in output
    assert "subject=" not in output
    assert "intent=" not in output


def test_prepare_visual_alone_produces_no_visible_output():
    state = _state_with_steps(
        StepResult(
            step_id="prepare",
            step_type="prepare_visual",
            status="success",
            output="internal metadata only",
        ),
    )

    output = assemble_final_output(state)

    assert output == "Aucun résultat exploitable n'a été produit."


# --- Internal metadata fields do not leak into final output ---

def test_internal_meta_keys_not_in_final_output():
    meta = {
        "subject_type": "product",
        "render_intent": "packshot",
        "style_flags": ["luxury"],
        "workflow_reason": "workflow=object_basic_v1 | subject=product",
        "parameters": {"width": 1024, "height": 1024},
    }
    meta_as_text = str(meta)

    state = _state_with_steps(
        StepResult(
            step_id="prepare",
            step_type="prepare_visual",
            status="success",
            output=meta_as_text,
            meta=meta,
        ),
        StepResult(
            step_id="comfy",
            step_type="tool_comfyui",
            status="success",
            output="IMAGE_GENERATED",
        ),
    )

    output = assemble_final_output(state)

    for key in _INTERNAL_META_KEYS:
        assert key not in output, f"Internal meta key '{key}' leaked into final output"


# --- prepare_visual metadata is still accessible in step_results ---

def test_prepare_visual_meta_accessible_in_step_results():
    meta = {
        "subject_type": "character",
        "render_intent": "portrait",
        "style_flags": ["cinematic"],
        "workflow_reason": "workflow=portrait_v1",
        "parameters": {"width": 512, "height": 768},
    }

    state = _state_with_steps(
        StepResult(
            step_id="prepare",
            step_type="prepare_visual",
            status="success",
            output="internal",
            meta=meta,
        ),
    )

    prepare = next(r for r in state.step_results if r.step_type == "prepare_visual")

    assert prepare.meta["subject_type"] == "character"
    assert prepare.meta["render_intent"] == "portrait"
    assert prepare.meta["workflow_reason"] == "workflow=portrait_v1"
    assert prepare.meta["parameters"]["width"] == 512
