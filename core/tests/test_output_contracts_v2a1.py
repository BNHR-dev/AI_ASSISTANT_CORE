from app.engine.output_contracts import get_output_contract, render_output_contract


def test_build_contract_exposes_expected_sections():
    contract = get_output_contract("build")

    assert contract["sections"] == ["Objectif", "Code", "Tests rapides", "Usage"]
    assert any("bloc de code" in rule.lower() for rule in contract["rules"])


def test_unknown_contract_falls_back_to_default_description():
    contract = get_output_contract("unknown_task", "format custom")

    assert contract["description"] == "format custom"
    assert contract["sections"] == ["Réponse"]


def test_rendered_contract_contains_numbered_titles():
    rendered = render_output_contract("architecture")

    assert "Format attendu" in rendered
    assert "1. Options" in rendered
    assert "3. Décision recommandée" in rendered


def test_build_contract_adds_copy_pasteable_guardrails():
    contract = get_output_contract("build")

    assert any("copiable-collable" in rule.lower() for rule in contract["rules"])
    assert any("hypothèses" in rule.lower() for rule in contract["rules"])
