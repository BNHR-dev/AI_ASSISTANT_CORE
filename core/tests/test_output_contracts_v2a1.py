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


def test_build_contract_forbids_tfidf_as_silent_embedding_substitute():
    contract = get_output_contract("build")
    rules_text = " ".join(contract["rules"]).lower()

    assert "embeddings" in rules_text or "vecteurs numériques" in rules_text
    assert "tfidfvectorizer" in rules_text or "countvectorizer" in rules_text
    assert "silencieusement" in rules_text or "alternative" in rules_text
    assert "vectorizer" in rules_text or "approximation" in rules_text


def test_build_contract_has_blender_bpy_rule():
    contract = get_output_contract("build")
    rules_text = " ".join(contract["rules"]).lower()

    assert "blender" in rules_text or "bpy" in rules_text
    assert "import bpy" in rules_text
    assert "bpy.ops" in rules_text or "bpy.data" in rules_text
    assert "bpy.ops.render.render" in rules_text


def test_image_generation_contract_documents_artefact_and_fallback():
    contract = get_output_contract("image_generation")
    rules_text = " ".join(contract["rules"]).lower()

    assert "comfyui" in rules_text or "moteur de génération" in rules_text
    assert "png" in rules_text or "jpeg" in rules_text or "artefact image" in rules_text
    assert "erreur" in rules_text or "dégradation" in rules_text


def test_web_research_contract_requires_source_domain_and_date():
    contract = get_output_contract("web_research")
    rules_text = " ".join(contract["rules"]).lower()

    assert "domaine" in rules_text or "url courte" in rules_text
    assert "date de publication" in rules_text


def test_web_research_contract_has_insufficiency_fallback_rule():
    contract = get_output_contract("web_research")
    rules_text = " ".join(contract["rules"]).lower()

    assert "insuffisants" in rules_text or "insuffisant" in rules_text
    assert "explicitement" in rules_text
