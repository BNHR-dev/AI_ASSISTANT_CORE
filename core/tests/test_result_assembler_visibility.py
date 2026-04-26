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


# GAP C — fallback sur le premier output en erreur


def test_assembler_falls_back_to_error_output_when_no_success():
    state = ExecutionState(
        message="test",
        decision={},
        plan=ExecutionPlan(task_type="build", steps=[]),
        step_results=[
            StepResult(
                step_id="a",
                step_type="llm_primary",
                status="error",
                output="Le moteur LLM local n'a pas pu répondre.",
            ),
        ],
    )

    output = assemble_final_output(state)
    assert output == "Le moteur LLM local n'a pas pu répondre."


# GAP D — fallback sur message par défaut quand aucun output exploitable


def test_assembler_returns_default_message_when_no_output_at_all():
    state = ExecutionState(
        message="test",
        decision={},
        plan=ExecutionPlan(task_type="build", steps=[]),
        step_results=[
            StepResult(
                step_id="a",
                step_type="llm_primary",
                status="error",
                output=None,
            ),
        ],
    )

    output = assemble_final_output(state)
    assert output == "Aucun résultat exploitable n'a été produit."
