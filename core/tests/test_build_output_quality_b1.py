from app.engine.output_contracts import get_output_contract
from app.engine.planner_types import ExecutionPlan, ExecutionState, StepResult
from app.engine.result_assembler import assemble_final_output


def _build_rules_text():
    contract = get_output_contract("build")
    return " ".join(contract["rules"]).lower()


def test_build_contract_requires_code_block():
    rules_text = _build_rules_text()
    assert "bloc de code" in rules_text


def test_build_contract_forbids_todos_and_pseudocode():
    rules_text = _build_rules_text()
    assert "todo" in rules_text
    assert "pseudo-code" in rules_text or "pseudocode" in rules_text


def test_build_contract_requires_quick_tests():
    contract = get_output_contract("build")
    sections_lower = [s.lower() for s in contract["sections"]]
    assert any("test" in s for s in sections_lower)


def test_build_contract_requires_exploitable_output():
    rules_text = _build_rules_text()
    assert "exploitable" in rules_text


def test_assembler_single_step_build_returns_llm_output_only():
    state = ExecutionState(
        message="écris un script Python",
        decision={},
        plan=ExecutionPlan(task_type="build", steps=[]),
        step_results=[
            StepResult(
                step_id="step_primary",
                step_type="llm_primary",
                status="success",
                output="import os\nprint('hello')",
            ),
        ],
    )
    output = assemble_final_output(state)
    assert output == "import os\nprint('hello')"
    assert "step_primary" not in output
    assert "task_type" not in output
    assert "decision" not in output


def test_blender_bpy_nonregression_contract_still_holds():
    rules_text = _build_rules_text()
    assert "import bpy" in rules_text
    assert "bpy.ops" in rules_text
    assert "bpy.data" in rules_text
    assert "bpy.context" in rules_text
    assert "bpy.ops.render.render" in rules_text
    assert "sauf demande explicite" in rules_text
    assert "keyframe_insert" in rules_text
    assert "data.extrude" in rules_text
