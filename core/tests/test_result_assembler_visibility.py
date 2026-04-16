from app.engine.planner_types import ExecutionPlan, ExecutionState, StepResult
from app.engine.result_assembler import assemble_final_output


def test_assembler_hides_technical_steps():
    state = ExecutionState(
        message="test",
        decision={},
        plan=ExecutionPlan(task_type="web_research", steps=[]),
        step_results=[
            StepResult(step_id="a", step_type="tool_web_search", status="success", output="5 résultats web récupérés."),
            StepResult(step_id="b", step_type="llm_synthesis", status="success", output="SYNTHÈSE FINALE"),
        ],
    )

    output = assemble_final_output(state)
    assert output == "SYNTHÈSE FINALE"
