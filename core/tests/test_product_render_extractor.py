"""
H.5.2 — Tests unitaires de product_render_extractor.

Aucune dépendance Ollama / Blender / réseau / filesystem outputs.
Toute interaction LLM passe par `generate_fn` injecté.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.engine.product_render_extractor import (
    DEFAULT_EXTRACTION_MODEL,
    FALLBACK_INTENT,
    ProductRenderExtractionResult,
    _extract_balanced_braces,
    _extract_json_block,
    build_extraction_prompt,
    extract_product_render_intent,
    parse_product_render_intent_from_text,
)
from app.engine.product_render_ir import ProductRenderIntent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VALID_JSON_TEXT = (
    '{"schema_version":"v0",'
    '"subject":{"kind":"bottle","color":"amber","material":"glass"},'
    '"backdrop":{"color":"neutral_gray"}}'
)


def _fake_gen(response: str):
    """Factory : retourne une callable generate_fn qui renvoie toujours `response`."""
    def _fn(model: str, prompt: str) -> str:
        return response
    return _fn


# ---------------------------------------------------------------------------
# build_extraction_prompt — pur
# ---------------------------------------------------------------------------

class TestBuildExtractionPrompt:

    def test_prompt_contains_user_message(self):
        p = build_extraction_prompt("bouteille de parfum ambrée sur fond gris")
        assert "bouteille de parfum ambrée sur fond gris" in p

    def test_prompt_mentions_all_subject_kinds(self):
        p = build_extraction_prompt("anything")
        for k in ("bottle", "jar", "box", "tube", "cylinder", "sphere"):
            assert k in p, f"kind {k!r} missing from prompt"

    def test_prompt_mentions_all_materials(self):
        p = build_extraction_prompt("anything")
        for m in ("matte", "glossy", "glass", "metallic"):
            assert m in p, f"material {m!r} missing from prompt"

    def test_prompt_mentions_palette_entries(self):
        p = build_extraction_prompt("anything")
        # Vérifier quelques entrées canoniques de la palette
        for c in ("amber", "neutral_gray", "white", "black", "blue"):
            assert c in p, f"palette color {c!r} missing from prompt"

    def test_prompt_demands_json_only(self):
        p = build_extraction_prompt("anything")
        assert "JSON" in p
        assert "UNIQUEMENT" in p or "uniquement" in p.lower()

    def test_prompt_handles_empty_message(self):
        # Ne doit pas crasher
        p = build_extraction_prompt("")
        assert isinstance(p, str)
        assert len(p) > 0


# ---------------------------------------------------------------------------
# _extract_balanced_braces — pur
# ---------------------------------------------------------------------------

class TestExtractBalancedBraces:

    def test_simple_object(self):
        assert _extract_balanced_braces('{"a":1}') == '{"a":1}'

    def test_nested_object(self):
        text = '{"a":{"b":2},"c":3}'
        assert _extract_balanced_braces(text) == text

    def test_object_with_surrounding_text(self):
        text = 'foo {"a":1} bar'
        assert _extract_balanced_braces(text) == '{"a":1}'

    def test_no_braces_returns_none(self):
        assert _extract_balanced_braces("no braces here") is None

    def test_only_opening_brace_returns_none(self):
        assert _extract_balanced_braces('{"a":1') is None

    def test_ignores_braces_inside_string(self):
        # Une accolade dans une chaîne JSON ne doit pas perturber le comptage
        text = '{"comment":"contains } brace","x":1}'
        assert _extract_balanced_braces(text) == text

    def test_returns_first_object_when_multiple(self):
        text = '{"a":1} {"b":2}'
        assert _extract_balanced_braces(text) == '{"a":1}'


# ---------------------------------------------------------------------------
# _extract_json_block — pur
# ---------------------------------------------------------------------------

class TestExtractJsonBlock:

    def test_extracts_from_markdown_json_fence(self):
        text = "Voici le résultat :\n```json\n" + VALID_JSON_TEXT + "\n```\nFin."
        out = _extract_json_block(text)
        assert out is not None
        assert out.startswith("{") and out.endswith("}")

    def test_extracts_from_markdown_unspecified_fence(self):
        text = "```\n" + VALID_JSON_TEXT + "\n```"
        out = _extract_json_block(text)
        assert out is not None
        assert out.startswith("{")

    def test_extracts_from_python_fence(self):
        text = "```python\n" + VALID_JSON_TEXT + "\n```"
        out = _extract_json_block(text)
        assert out is not None

    def test_extracts_from_raw_json(self):
        out = _extract_json_block(VALID_JSON_TEXT)
        assert out == VALID_JSON_TEXT

    def test_extracts_from_text_with_parasitic_prefix_suffix(self):
        text = "Sure! Here is the JSON: " + VALID_JSON_TEXT + " Hope that helps."
        out = _extract_json_block(text)
        assert out is not None
        assert out.startswith("{")

    def test_returns_none_on_empty(self):
        assert _extract_json_block("") is None
        assert _extract_json_block(None) is None  # type: ignore[arg-type]

    def test_returns_none_on_pure_garbage(self):
        assert _extract_json_block("absolutely no json here") is None


# ---------------------------------------------------------------------------
# parse_product_render_intent_from_text — pur
# ---------------------------------------------------------------------------

class TestParseProductRenderIntentFromText:

    def test_pure_valid_json_parses(self):
        result = parse_product_render_intent_from_text(VALID_JSON_TEXT)
        assert result.status == "parsed"
        assert result.error is None
        assert result.intent.subject.kind == "bottle"
        assert result.intent.subject.color == "amber"
        assert result.intent.subject.material == "glass"
        assert result.intent.backdrop.color == "neutral_gray"
        assert result.extracted_json is not None

    def test_markdown_json_block_parses(self):
        text = "Voici le JSON :\n```json\n" + VALID_JSON_TEXT + "\n```\nFin"
        result = parse_product_render_intent_from_text(text)
        assert result.status == "parsed"
        assert result.intent.subject.kind == "bottle"

    def test_text_with_parasitic_wrapping_parses(self):
        text = "Bla bla " + VALID_JSON_TEXT + " trailing commentary"
        result = parse_product_render_intent_from_text(text)
        assert result.status == "parsed"

    def test_invalid_json_falls_back(self):
        result = parse_product_render_intent_from_text("not json at all")
        assert result.status == "fallback"
        assert "no_json_block_found" in result.error

    def test_malformed_json_falls_back(self):
        result = parse_product_render_intent_from_text('{"a": ')  # JSON incomplet
        assert result.status == "fallback"
        assert "no_json_block_found" in result.error or "json_decode_error" in result.error

    def test_empty_string_falls_back(self):
        result = parse_product_render_intent_from_text("")
        assert result.status == "fallback"
        assert result.error == "empty_response"

    def test_none_input_falls_back(self):
        result = parse_product_render_intent_from_text(None)
        assert result.status == "fallback"
        assert result.error == "empty_response"

    def test_whitespace_only_falls_back(self):
        result = parse_product_render_intent_from_text("   \n  \t  ")
        assert result.status == "fallback"
        assert result.error == "empty_response"

    def test_json_array_falls_back(self):
        result = parse_product_render_intent_from_text("[1, 2, 3]")
        # Le parser extrait { en priorité, ne trouve pas → fallback
        assert result.status == "fallback"

    def test_json_scalar_null_falls_back(self):
        result = parse_product_render_intent_from_text("null")
        assert result.status == "fallback"

    def test_invalid_kind_enum_falls_back(self):
        text = (
            '{"schema_version":"v0",'
            '"subject":{"kind":"rocket","color":"amber","material":"glass"},'
            '"backdrop":{"color":"neutral_gray"}}'
        )
        result = parse_product_render_intent_from_text(text)
        assert result.status == "fallback"
        assert "pydantic_validation_error" in result.error
        # Le JSON décodé est conservé pour debug
        assert result.extracted_json is not None
        assert result.extracted_json["subject"]["kind"] == "rocket"

    def test_invalid_material_enum_falls_back(self):
        text = (
            '{"schema_version":"v0",'
            '"subject":{"kind":"bottle","color":"amber","material":"velvet"},'
            '"backdrop":{"color":"neutral_gray"}}'
        )
        result = parse_product_render_intent_from_text(text)
        assert result.status == "fallback"

    def test_invalid_color_falls_back(self):
        text = (
            '{"schema_version":"v0",'
            '"subject":{"kind":"bottle","color":"amber-tinted","material":"glass"},'
            '"backdrop":{"color":"neutral_gray"}}'
        )
        result = parse_product_render_intent_from_text(text)
        assert result.status == "fallback"

    def test_missing_required_field_falls_back(self):
        text = '{"schema_version":"v0","subject":{"kind":"bottle"}}'
        result = parse_product_render_intent_from_text(text)
        assert result.status == "fallback"

    def test_extra_field_falls_back(self):
        # extra="forbid" en V0 → un champ surnuméraire fait échouer la validation
        text = (
            '{"schema_version":"v0",'
            '"subject":{"kind":"bottle","color":"amber","material":"glass","height_m":0.2},'
            '"backdrop":{"color":"neutral_gray"}}'
        )
        result = parse_product_render_intent_from_text(text)
        assert result.status == "fallback"

    def test_hex_color_accepted(self):
        text = (
            '{"schema_version":"v0",'
            '"subject":{"kind":"sphere","color":"#a83232","material":"matte"},'
            '"backdrop":{"color":"#f0f0f0"}}'
        )
        result = parse_product_render_intent_from_text(text)
        assert result.status == "parsed"
        assert result.intent.subject.color == "#a83232"

    def test_fallback_intent_is_always_valid_pydantic(self):
        result = parse_product_render_intent_from_text("absolute garbage")
        assert isinstance(result.intent, ProductRenderIntent)

    def test_fallback_intent_matches_h51_canonical_case(self):
        """Le fallback doit être l'IR canonique H.5.1 (bottle/amber/glass/neutral_gray)."""
        result = parse_product_render_intent_from_text("garbage")
        assert result.intent.subject.kind == "bottle"
        assert result.intent.subject.color == "amber"
        assert result.intent.subject.material == "glass"
        assert result.intent.backdrop.color == "neutral_gray"

    def test_model_propagated_to_result(self):
        result = parse_product_render_intent_from_text(VALID_JSON_TEXT, model="qwen2.5-coder:7b")
        assert result.model == "qwen2.5-coder:7b"

    def test_model_none_by_default(self):
        result = parse_product_render_intent_from_text(VALID_JSON_TEXT)
        assert result.model is None


# ---------------------------------------------------------------------------
# extract_product_render_intent — avec generate_fn injecté
# ---------------------------------------------------------------------------

class TestExtractProductRenderIntent:

    def test_mocked_llm_valid_json(self):
        result = extract_product_render_intent(
            "bouteille de parfum ambrée en verre sur fond gris",
            generate_fn=_fake_gen(VALID_JSON_TEXT),
        )
        assert result.status == "parsed"
        assert result.intent.subject.kind == "bottle"
        assert result.intent.subject.color == "amber"
        assert result.intent.subject.material == "glass"
        assert result.intent.backdrop.color == "neutral_gray"
        assert result.model == DEFAULT_EXTRACTION_MODEL

    def test_mocked_llm_different_ir(self):
        custom = (
            '{"schema_version":"v0",'
            '"subject":{"kind":"jar","color":"red","material":"matte"},'
            '"backdrop":{"color":"black"}}'
        )
        result = extract_product_render_intent(
            "petit pot rouge mat sur fond noir",
            generate_fn=_fake_gen(custom),
        )
        assert result.status == "parsed"
        assert result.intent.subject.kind == "jar"
        assert result.intent.subject.color == "red"
        assert result.intent.subject.material == "matte"
        assert result.intent.backdrop.color == "black"

    def test_mocked_llm_returns_markdown_block(self):
        wrapped = "Bien sûr ! Voici :\n```json\n" + VALID_JSON_TEXT + "\n```"
        result = extract_product_render_intent(
            "anything", generate_fn=_fake_gen(wrapped),
        )
        assert result.status == "parsed"

    def test_mocked_llm_returns_garbage(self):
        result = extract_product_render_intent(
            "anything", generate_fn=_fake_gen("no json here at all"),
        )
        assert result.status == "fallback"
        assert result.intent == FALLBACK_INTENT

    def test_mocked_llm_returns_empty(self):
        result = extract_product_render_intent(
            "anything", generate_fn=_fake_gen(""),
        )
        assert result.status == "fallback"

    def test_mocked_llm_raises_exception_falls_back(self):
        def boom(model, prompt):
            raise RuntimeError("ollama unavailable")
        result = extract_product_render_intent("anything", generate_fn=boom)
        assert result.status == "fallback"
        assert "llm_call_error" in result.error
        assert "RuntimeError" in result.error
        assert result.raw_response is None
        assert result.intent == FALLBACK_INTENT
        # Le modèle reste tracé
        assert result.model == DEFAULT_EXTRACTION_MODEL

    def test_custom_model_propagated(self):
        result = extract_product_render_intent(
            "anything", model="llama3", generate_fn=_fake_gen(VALID_JSON_TEXT),
        )
        assert result.model == "llama3"

    def test_extractor_passes_user_message_to_generate_fn(self):
        captured = {}
        def capture(model, prompt):
            captured["model"] = model
            captured["prompt"] = prompt
            return VALID_JSON_TEXT
        extract_product_render_intent("ma bouteille spéciale", generate_fn=capture)
        assert "ma bouteille spéciale" in captured["prompt"]

    def test_extractor_default_model_is_qwen(self):
        captured = {}
        def capture(model, prompt):
            captured["model"] = model
            return VALID_JSON_TEXT
        extract_product_render_intent("x", generate_fn=capture)
        assert captured["model"] == DEFAULT_EXTRACTION_MODEL
        assert captured["model"] == "qwen2.5-coder:7b"


# ---------------------------------------------------------------------------
# Robustesse : aucune entrée n'a le droit de faire crasher l'extracteur
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_response", [
    "",
    "   ",
    "\n\n\n",
    "🦄",
    "null",
    "true",
    "false",
    "42",
    "[]",
    "[1,2,3]",
    "{",
    "}",
    "{{}}",
    '{"x":',
    "{}",
    '{"unknown":1}',
    '{"schema_version":"v99"}',
    '{"schema_version":"v0","subject":"not-a-dict","backdrop":{"color":"white"}}',
    '{"schema_version":"v0","subject":{"kind":"bottle"},"backdrop":{}}',
    "```json\nnot json\n```",
    "```",
    "```\n\n```",
    "<html><body>{}</body></html>",
    "Sure! " * 100 + " {} " + "Cheers! " * 100,
])
def test_extractor_never_crashes_on_bad_response(bad_response):
    """Garantie absolue : aucune sortie LLM ne doit faire crasher l'extracteur.
    Le résultat doit toujours être un ProductRenderExtractionResult avec un
    `intent` valide typé `ProductRenderIntent`."""
    result = extract_product_render_intent("anything", generate_fn=_fake_gen(bad_response))
    assert isinstance(result, ProductRenderExtractionResult)
    assert isinstance(result.intent, ProductRenderIntent)
    # Le résultat est soit parsed soit fallback, jamais un autre statut
    assert result.status in ("parsed", "fallback")


# ---------------------------------------------------------------------------
# Le builder H.5.1 accepte l'IR retournée (parsed ou fallback)
# ---------------------------------------------------------------------------

def test_parsed_intent_consumable_by_builder():
    """L'IR retournée doit pouvoir être consommée par le builder H.5.1
    sans modification (bouclage IR → builder → bpy script)."""
    from app.engine.product_render_builder import build_product_render_scene_script
    result = extract_product_render_intent(
        "anything", generate_fn=_fake_gen(VALID_JSON_TEXT),
    )
    script = build_product_render_scene_script(result.intent)
    assert "import bpy" in script
    assert "Product_Subject" in script


def test_fallback_intent_consumable_by_builder():
    """Le fallback doit pouvoir être consommé par le builder H.5.1 — sinon
    la chaîne extracteur → builder casse silencieusement quand le LLM échoue."""
    from app.engine.product_render_builder import build_product_render_scene_script
    result = extract_product_render_intent(
        "anything", generate_fn=_fake_gen("garbage"),
    )
    assert result.status == "fallback"
    script = build_product_render_scene_script(result.intent)
    assert "import bpy" in script
    assert "Product_Subject" in script


# ---------------------------------------------------------------------------
# Garde-fous d'imports (cohérence avec H.5.1 et Décision 11)
# ---------------------------------------------------------------------------

def test_extractor_does_not_import_router_planner_executor_openai_compat():
    """L'extracteur ne doit JAMAIS toucher au noyau."""
    import app.engine.product_render_extractor as mod
    source = Path(mod.__file__).read_text(encoding="utf-8")
    forbidden = (
        "from app.engine.router",
        "import app.engine.router",
        "from app.engine.planner",
        "import app.engine.planner",
        "from app.engine.executor",
        "import app.engine.executor",
        "from app.openai_compat",
        "import app.openai_compat",
    )
    for imp in forbidden:
        assert imp not in source, f"Forbidden import in extractor: {imp}"


def test_extractor_does_not_import_blender_client():
    """L'extracteur reste indépendant du pipeline Blender existant
    (le branchement est l'objet de H.5.3, pas de H.5.2)."""
    import app.engine.product_render_extractor as mod
    source = Path(mod.__file__).read_text(encoding="utf-8")
    assert "from app.clients.blender_client" not in source
    assert "import app.clients.blender_client" not in source


def test_blender_client_h53_wiring_is_intentional():
    """H.5.2 livrait l'extracteur comme brique isolée (PAS branché dans
    blender_client). H.5.3 câble explicitement extract_product_render_intent
    et build_product_render_scene_script dans `build_blender_script`. Ce test
    acte cette transition : le branchement est intentionnel et documenté.

    Garde-fou maintenu pour la version H.5.2 : ce test version H.5.3 confirme
    le câblage attendu mais préserve la séparation des responsabilités du
    module extractor (lui ne dépend pas de blender_client — voir
    test_extractor_does_not_import_blender_client)."""
    import app.clients.blender_client as bc
    assert hasattr(bc, "build_blender_script")
    source = Path(bc.__file__).read_text(encoding="utf-8")
    # H.5.3 — câblage attendu :
    assert "extract_product_render_intent" in source, (
        "H.5.3 doit importer extract_product_render_intent dans blender_client"
    )
    assert "build_product_render_scene_script" in source, (
        "H.5.3 doit importer build_product_render_scene_script dans blender_client"
    )
    # Constantes de traçabilité du chemin
    assert "product_render_ir_builder" in source
    assert "legacy_llm_bpy_scaffold" in source
    # Feature flag de rollback runtime
    assert "BLENDER_USE_PRODUCT_RENDER_IR" in source


def test_extractor_does_not_touch_filesystem_outputs():
    """L'extracteur ne doit écrire nulle part : il retourne juste un objet."""
    import app.engine.product_render_extractor as mod
    source = Path(mod.__file__).read_text(encoding="utf-8")
    forbidden_io = (
        "open(",
        "Path(",
        "os.makedirs",
        "shutil.",
        "subprocess.",
    )
    for io in forbidden_io:
        assert io not in source, f"Unexpected I/O / subprocess in extractor: {io}"


# ---------------------------------------------------------------------------
# H.5.4 — Extractor V1
# ---------------------------------------------------------------------------


VALID_V1_JSON_TEXT = (
    '{"schema_version":"v1",'
    '"subject":{"kind":"bottle","color":"amber","material":"glass",'
    '"shape":"cylindrical","cap":"present","transparency":"glass"},'
    '"backdrop":{"color":"neutral_gray"},'
    '"framing":"close_packshot"}'
)


class TestExtractorPromptV1:

    def test_prompt_mentions_schema_v1(self):
        p = build_extraction_prompt("anything")
        assert '"schema_version": "v1"' in p or "v1" in p

    def test_prompt_lists_all_shape_values(self):
        p = build_extraction_prompt("anything")
        for v in ("cylindrical", "rectangular", "rounded"):
            assert v in p, f"shape {v!r} missing from prompt"

    def test_prompt_lists_all_cap_values(self):
        p = build_extraction_prompt("anything")
        for v in ("present", "absent"):
            assert v in p, f"cap {v!r} missing from prompt"

    def test_prompt_lists_all_transparency_values(self):
        p = build_extraction_prompt("anything")
        for v in ("opaque", "translucent", "glass"):
            assert v in p, f"transparency {v!r} missing from prompt"

    def test_prompt_lists_all_framing_values(self):
        p = build_extraction_prompt("anything")
        for v in ("close_packshot", "medium"):
            assert v in p, f"framing {v!r} missing from prompt"


class TestExtractorParseV1:

    def test_valid_v1_json_parses(self):
        result = parse_product_render_intent_from_text(VALID_V1_JSON_TEXT)
        assert result.status == "parsed"
        assert result.intent.schema_version == "v1"
        assert result.intent.subject.shape == "cylindrical"
        assert result.intent.subject.cap == "present"
        assert result.intent.subject.transparency == "glass"
        assert result.intent.framing == "close_packshot"

    def test_v1_partial_fields_use_pydantic_defaults_none(self):
        """V1 sans les 4 champs nouveaux : valide, les champs restent None,
        le builder appliquera les défauts."""
        text = (
            '{"schema_version":"v1",'
            '"subject":{"kind":"bottle","color":"amber","material":"glass"},'
            '"backdrop":{"color":"neutral_gray"}}'
        )
        result = parse_product_render_intent_from_text(text)
        assert result.status == "parsed"
        assert result.intent.subject.shape is None
        assert result.intent.subject.cap is None
        assert result.intent.subject.transparency is None
        assert result.intent.framing is None

    def test_invalid_shape_falls_back(self):
        text = (
            '{"schema_version":"v1",'
            '"subject":{"kind":"bottle","color":"amber","material":"glass",'
            '"shape":"square"},'
            '"backdrop":{"color":"neutral_gray"}}'
        )
        result = parse_product_render_intent_from_text(text)
        assert result.status == "fallback"

    def test_invalid_cap_falls_back(self):
        text = (
            '{"schema_version":"v1",'
            '"subject":{"kind":"bottle","color":"amber","material":"glass",'
            '"cap":"maybe"},'
            '"backdrop":{"color":"neutral_gray"}}'
        )
        result = parse_product_render_intent_from_text(text)
        assert result.status == "fallback"

    def test_invalid_transparency_falls_back(self):
        text = (
            '{"schema_version":"v1",'
            '"subject":{"kind":"bottle","color":"amber","material":"glass",'
            '"transparency":"cloudy"},'
            '"backdrop":{"color":"neutral_gray"}}'
        )
        result = parse_product_render_intent_from_text(text)
        assert result.status == "fallback"

    def test_invalid_framing_falls_back(self):
        text = (
            '{"schema_version":"v1",'
            '"subject":{"kind":"bottle","color":"amber","material":"glass"},'
            '"backdrop":{"color":"neutral_gray"},'
            '"framing":"wide"}'
        )
        result = parse_product_render_intent_from_text(text)
        assert result.status == "fallback"

    def test_v0_intent_still_parses_unchanged(self):
        """Compat : un IR V0 strict reste valide."""
        result = parse_product_render_intent_from_text(VALID_JSON_TEXT)
        assert result.status == "parsed"
        assert result.intent.schema_version == "v0"


class TestExtractorEndToEndV1:

    def test_mocked_llm_v1_canonical_smoke_prompt(self):
        """Cas canonique smoke H.5.4 : bouteille de parfum en verre ambré
        sur socle, packshot. Le LLM mocké renvoie l'IR V1 attendue."""
        smoke_prompt = (
            "Crée une scène Blender de prévisualisation 3D : "
            "bouteille de parfum en verre ambré sur socle, "
            "rendu produit packshot, fond neutre, éclairage studio doux, "
            "composition centrée, style minimaliste réaliste"
        )
        result = extract_product_render_intent(
            smoke_prompt, generate_fn=_fake_gen(VALID_V1_JSON_TEXT),
        )
        assert result.status == "parsed"
        assert result.intent.schema_version == "v1"
        assert result.intent.subject.kind == "bottle"
        assert result.intent.subject.color == "amber"
        assert result.intent.subject.material == "glass"
        assert result.intent.subject.transparency == "glass"
        assert result.intent.subject.cap == "present"
        assert result.intent.framing == "close_packshot"

    def test_mocked_llm_garbage_falls_back_to_v0_canonical(self):
        """Le fallback doit rester l'IR canonique H.5.1 (v0) — pas de
        régression de comportement après H.5.4."""
        result = extract_product_render_intent(
            "anything", generate_fn=_fake_gen("absolute garbage"),
        )
        assert result.status == "fallback"
        assert result.intent.schema_version == "v0"

    def test_parsed_v1_intent_consumable_by_builder(self):
        """Bouclage : extracteur V1 → builder V1 → script bpy valide."""
        from app.engine.product_render_builder import build_product_render_scene_script
        result = extract_product_render_intent(
            "anything", generate_fn=_fake_gen(VALID_V1_JSON_TEXT),
        )
        script = build_product_render_scene_script(result.intent)
        assert "import bpy" in script
        assert "Product_Subject" in script
        assert "Product_Cap" in script   # cap=present
        assert "1.0" in script           # transmission=1.0 (glass)
