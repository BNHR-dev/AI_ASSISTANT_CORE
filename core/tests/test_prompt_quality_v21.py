from app.engine.prompt_builder import build_primary_prompt, build_second_call_prompt


def test_primary_prompt_suppresses_code_when_second_step_exists():
    prompt = build_primary_prompt(
        agent="AGENT_PROF_IA",
        output_format="définition + image mentale + exemple concret",
        message="explique moi les embeddings avec un exemple python",
        task_type="explain_basic",
        suppress_code=True,
    )

    assert "ne produis pas le code final détaillé".lower() in prompt.lower()
    assert "étape 1 d'un pipeline multi-step".lower() in prompt.lower()


def test_second_call_prompt_focuses_on_livrable():
    prompt = build_second_call_prompt(
        agent="AGENT_BUILDER_IA",
        output_format="module python + structure + instructions de test + usage",
        user_question="explique moi les embeddings avec un exemple python",
        primary_output="EXPLAIN_OUTPUT",
        requested_task_type="build",
    )

    assert "étape 2 d'un pipeline multi-step".lower() in prompt.lower()
    assert "produis directement le livrable technique".lower() in prompt.lower()
    assert "évite de refaire toute la théorie".lower() in prompt.lower()
