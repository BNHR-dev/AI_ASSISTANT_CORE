"""
Tests — H.5.4.1 : Robustesse du déclenchement product_render IR V1.

Couvre :
- détection product_render élargie (parse_artistic_intent + select_template_*)
  pour les prompts B (flacon rectangulaire packshot cinématographique) et
  C (fiole arrondie prévisualisation cinématographique), plus deux variantes
  (pot cosmétique, bloc produit rectangulaire) et un test négatif strict.
- routing blender_client : pipeline_path + traces ir_attempted /
  extraction_status / extraction_reason selon le comportement de l'extracteur.
- propagation dans manifest.future (pipeline_path, product_render_intent,
  product_render_ir_attempted, product_render_extraction_status,
  product_render_extraction_reason).
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.clients.blender_client import (
    PIPELINE_PATH_BUILDER,
    PIPELINE_PATH_LEGACY,
    build_blender_script,
)
from app.engine.artifact_manifest import build_blender_manifest
from app.engine.artistic_intent import parse_artistic_intent
from app.engine.blender_templates import (
    TEMPLATE_PRODUCT_RENDER,
    get_template_name,
    get_template_name_from_intent,
    select_template,
    select_template_from_intent,
)
from app.engine.blender_types import BlenderRequest, BlenderResult
from app.engine.product_render_extractor import ProductRenderExtractionResult
from app.engine.product_render_ir import (
    BackdropIR,
    ProductRenderIntent,
    ProductSubjectIR,
)


# ---------------------------------------------------------------------------
# Prompts canoniques H.5.4.1
# ---------------------------------------------------------------------------

# Smoke B — flacon rectangulaire verre transparent packshot cinématographique
PROMPT_B = (
    "Crée une scène Blender de prévisualisation 3D : flacon de parfum "
    "rectangulaire en verre transparent avec bouchon noir, objet héros, "
    "rendu packshot cinématographique, fond neutre sombre, cadrage proche, "
    "style élégant et réaliste"
)

# Smoke C — fiole arrondie verre translucide ambré prévisualisation cinématographique
PROMPT_C = (
    "Crée une scène Blender de prévisualisation 3D : petite fiole arrondie "
    "en verre translucide ambré avec bouchon métallique, objet de "
    "prévisualisation cinématographique, rendu réaliste, fond neutre, "
    "cadrage proche, ambiance sobre"
)

# Variantes additionnelles H.5.4.1
PROMPT_POT = "pot cosmétique blanc avec couvercle, rendu produit premium"
PROMPT_BLOC = "bloc produit rectangulaire noir mat sans bouchon, rendu packshot sobre"

# Test négatif — scène cinématographique dans une rue sous la pluie ;
# ne doit pas devenir product_render.
PROMPT_NEGATIVE = "scène cinématographique sombre dans une rue sous la pluie"


# ---------------------------------------------------------------------------
# Section 1 — Détection product_render élargie
# ---------------------------------------------------------------------------

class TestProductRenderDetectionH541:
    """Les prompts B/C/pot/bloc doivent être candidats product_render."""

    @pytest.mark.parametrize("prompt", [PROMPT_B, PROMPT_C, PROMPT_POT, PROMPT_BLOC])
    def test_intent_medium_is_product_render(self, prompt):
        intent = parse_artistic_intent(prompt)
        assert intent.medium == "product_render", (
            f"prompt should classify as product_render, got medium={intent.medium!r} "
            f"for prompt: {prompt!r}"
        )

    @pytest.mark.parametrize("prompt", [PROMPT_B, PROMPT_C, PROMPT_POT, PROMPT_BLOC])
    def test_template_resolves_to_product_render(self, prompt):
        """Soit via intent, soit via fallback message brut, le template
        product_render doit être sélectionné."""
        intent = parse_artistic_intent(prompt)
        tpl = get_template_name_from_intent(intent) or get_template_name(prompt)
        assert tpl == "product_render", (
            f"expected template_used='product_render', got {tpl!r} "
            f"for prompt: {prompt!r}"
        )

    def test_prompt_b_subject_is_product_compatible(self):
        intent = parse_artistic_intent(PROMPT_B)
        # "flacon de parfum" → subject_main="bouteille" (cf. _SUBJECT_RULES).
        assert intent.subject_main == "bouteille"

    def test_prompt_c_subject_is_product_compatible(self):
        intent = parse_artistic_intent(PROMPT_C)
        # "fiole" est désormais mappé sur "bouteille" via _SUBJECT_RULES.
        assert intent.subject_main == "bouteille"

    def test_prompt_pot_subject_is_pot(self):
        intent = parse_artistic_intent(PROMPT_POT)
        assert intent.subject_main == "pot"

    def test_prompt_bloc_subject_is_bloc(self):
        intent = parse_artistic_intent(PROMPT_BLOC)
        assert intent.subject_main == "bloc"


class TestProductRenderNegativeStrict:
    """Garde-fou : une scène cinématographique sans objet produit ne doit
    PAS devenir product_render."""

    def test_negative_prompt_not_product_render(self):
        intent = parse_artistic_intent(PROMPT_NEGATIVE)
        assert intent.medium != "product_render"

    def test_negative_prompt_no_product_template(self):
        intent = parse_artistic_intent(PROMPT_NEGATIVE)
        tpl_intent = select_template_from_intent(intent)
        tpl_msg = select_template(PROMPT_NEGATIVE)
        assert tpl_intent is not TEMPLATE_PRODUCT_RENDER
        assert tpl_msg is not TEMPLATE_PRODUCT_RENDER

    def test_cinematographique_alone_does_not_trigger_product(self):
        intent = parse_artistic_intent("scène cinématographique de nuit")
        assert intent.medium != "product_render"


# ---------------------------------------------------------------------------
# Section 2 — Routing blender_client (mocks)
# ---------------------------------------------------------------------------

_FAKE_ID = "test-h541-001"


def _parsed_extraction(message: str = "") -> ProductRenderExtractionResult:
    intent = ProductRenderIntent(
        schema_version="v1",
        subject=ProductSubjectIR(
            kind="bottle",
            color="amber",
            material="glass",
            shape="rectangular",
            cap="present",
            transparency="glass",
        ),
        backdrop=BackdropIR(color="neutral_gray"),
        framing="close_packshot",
    )
    return ProductRenderExtractionResult(
        intent=intent,
        status="parsed",
        raw_response="{...}",
        extracted_json=intent.model_dump(),
        error=None,
        model="qwen2.5-coder:7b",
    )


def _fallback_extraction(reason: str = "no_json_block_found") -> ProductRenderExtractionResult:
    # FALLBACK_INTENT est validé au chargement du module extractor ; ici on
    # construit un IR valide minimal V0 pour rester indépendant.
    intent = ProductRenderIntent(
        schema_version="v0",
        subject=ProductSubjectIR(kind="bottle", color="amber", material="glass"),
        backdrop=BackdropIR(color="neutral_gray"),
    )
    return ProductRenderExtractionResult(
        intent=intent,
        status="fallback",
        raw_response="garbage",
        extracted_json=None,
        error=reason,
        model="qwen2.5-coder:7b",
    )


def _build_with_mocks(message: str, extraction: ProductRenderExtractionResult | None):
    """
    Appelle build_blender_script en mockant tous les I/O externes ainsi que
    l'extracteur LLM. Si extraction is None, l'extracteur n'est pas mocké
    (utilisé pour les prompts non product_render — extracteur jamais appelé).
    """
    patches = [
        patch("app.clients.blender_client.generate_with_ollama",
              return_value="```python\nimport bpy\n```"),
        patch("app.clients.blender_client.write_intent_json", return_value=None),
        patch("pathlib.Path.mkdir"),
        patch("pathlib.Path.write_text"),
    ]
    if extraction is not None:
        patches.append(
            patch(
                "app.clients.blender_client.extract_product_render_intent",
                return_value=extraction,
            )
        )

    for p in patches:
        p.start()
    try:
        return build_blender_script(message=message, context={}, request_id=_FAKE_ID)
    finally:
        for p in patches:
            p.stop()


class TestBlenderClientRoutingH541:
    """Routing + traces selon le statut d'extraction (parsed / fallback / skipped)."""

    def test_parsed_extraction_routes_to_builder(self):
        request = _build_with_mocks(PROMPT_B, _parsed_extraction())
        assert request.template_used == "product_render"
        assert request.pipeline_path == PIPELINE_PATH_BUILDER
        assert request.product_render_intent is not None
        assert request.product_render_ir_attempted is True
        assert request.product_render_extraction_status == "parsed"
        assert request.product_render_extraction_reason is None

    def test_fallback_extraction_routes_to_legacy_with_trace(self):
        request = _build_with_mocks(PROMPT_B, _fallback_extraction("no_json_block_found"))
        assert request.template_used == "product_render"
        assert request.pipeline_path == PIPELINE_PATH_LEGACY
        assert request.product_render_intent is None
        assert request.product_render_ir_attempted is True
        assert request.product_render_extraction_status == "fallback"
        assert request.product_render_extraction_reason == "no_json_block_found"

    def test_non_product_prompt_skips_extraction(self):
        # Prompt neutre — aucun match product_render.
        request = _build_with_mocks(PROMPT_NEGATIVE, None)
        assert request.template_used != "product_render"
        assert request.pipeline_path == PIPELINE_PATH_LEGACY
        assert request.product_render_ir_attempted is False
        assert request.product_render_extraction_status == "skipped"
        assert request.product_render_extraction_reason == "template_not_product_render"

    def test_smoke_b_with_parsed_extraction_routes_to_builder(self):
        request = _build_with_mocks(PROMPT_B, _parsed_extraction())
        assert request.template_used == "product_render"
        assert request.pipeline_path == PIPELINE_PATH_BUILDER
        assert request.product_render_intent["schema_version"] == "v1"

    def test_smoke_c_with_parsed_extraction_routes_to_builder(self):
        request = _build_with_mocks(PROMPT_C, _parsed_extraction())
        assert request.template_used == "product_render"
        assert request.pipeline_path == PIPELINE_PATH_BUILDER
        assert request.product_render_intent["schema_version"] == "v1"


# ---------------------------------------------------------------------------
# Section 3 — manifest.future contient les traces H.5.4.1
# ---------------------------------------------------------------------------

class TestManifestProductRenderTracesH541:
    """manifest.future doit exposer pipeline_path + product_render_intent +
    les trois nouvelles traces ir_attempted / extraction_status / reason."""

    def _make_result(self, output_dir: str, output_path: str) -> BlenderResult:
        return BlenderResult(
            status="success",
            request_id=_FAKE_ID,
            script_path=f"{output_dir}/scene.py",
            output_path=output_path,
            render_path=None,
            output_dir=output_dir,
            returncode=0,
            stdout=None,
            stderr=None,
            error=None,
        )

    def test_manifest_future_exposes_parsed_traces(self):
        output_dir = f"outputs/blender/{_FAKE_ID}"
        request = BlenderRequest(
            request_id=_FAKE_ID,
            script_content="import bpy",
            script_path=f"{output_dir}/scene.py",
            output_path=f"{output_dir}/scene.blend",
            render_path=f"{output_dir}/preview.png",
            output_dir=output_dir,
            timeout=60,
            source_prompt=PROMPT_B,
            template_used="product_render",
            pipeline_path=PIPELINE_PATH_BUILDER,
            product_render_intent={"schema_version": "v1"},
            product_render_ir_attempted=True,
            product_render_extraction_status="parsed",
            product_render_extraction_reason=None,
        )
        result = self._make_result(output_dir, request.output_path)
        manifest = build_blender_manifest(request, result)

        future = manifest["future"]
        assert future["pipeline_path"] == PIPELINE_PATH_BUILDER
        assert future["product_render_intent"] == {"schema_version": "v1"}
        assert future["product_render_ir_attempted"] is True
        assert future["product_render_extraction_status"] == "parsed"
        assert future["product_render_extraction_reason"] is None

    def test_manifest_future_exposes_fallback_traces(self):
        output_dir = f"outputs/blender/{_FAKE_ID}"
        request = BlenderRequest(
            request_id=_FAKE_ID,
            script_content="import bpy",
            script_path=f"{output_dir}/scene.py",
            output_path=f"{output_dir}/scene.blend",
            render_path=f"{output_dir}/preview.png",
            output_dir=output_dir,
            timeout=60,
            source_prompt=PROMPT_B,
            template_used="product_render",
            pipeline_path=PIPELINE_PATH_LEGACY,
            product_render_intent=None,
            product_render_ir_attempted=True,
            product_render_extraction_status="fallback",
            product_render_extraction_reason="no_json_block_found",
        )
        result = self._make_result(output_dir, request.output_path)
        manifest = build_blender_manifest(request, result)

        future = manifest["future"]
        assert future["pipeline_path"] == PIPELINE_PATH_LEGACY
        assert future["product_render_intent"] is None
        assert future["product_render_ir_attempted"] is True
        assert future["product_render_extraction_status"] == "fallback"
        assert future["product_render_extraction_reason"] == "no_json_block_found"

    def test_manifest_future_exposes_skipped_for_non_product(self):
        output_dir = f"outputs/blender/{_FAKE_ID}"
        request = BlenderRequest(
            request_id=_FAKE_ID,
            script_content="import bpy",
            script_path=f"{output_dir}/scene.py",
            output_path=f"{output_dir}/scene.blend",
            render_path=f"{output_dir}/preview.png",
            output_dir=output_dir,
            timeout=60,
            source_prompt=PROMPT_NEGATIVE,
            template_used=None,
            pipeline_path=PIPELINE_PATH_LEGACY,
            product_render_intent=None,
            product_render_ir_attempted=False,
            product_render_extraction_status="skipped",
            product_render_extraction_reason="template_not_product_render",
        )
        result = self._make_result(output_dir, request.output_path)
        manifest = build_blender_manifest(request, result)

        future = manifest["future"]
        assert future["pipeline_path"] == PIPELINE_PATH_LEGACY
        assert future["product_render_ir_attempted"] is False
        assert future["product_render_extraction_status"] == "skipped"
        assert future["product_render_extraction_reason"] == "template_not_product_render"

    def test_manifest_future_defaults_when_request_lacks_traces(self):
        """BlenderRequest construit sans nouveaux champs ne casse pas le manifest."""
        output_dir = f"outputs/blender/{_FAKE_ID}"
        request = BlenderRequest(
            request_id=_FAKE_ID,
            script_content="import bpy",
            script_path=f"{output_dir}/scene.py",
            output_path=f"{output_dir}/scene.blend",
            render_path=f"{output_dir}/preview.png",
            output_dir=output_dir,
            timeout=60,
        )
        result = self._make_result(output_dir, request.output_path)
        manifest = build_blender_manifest(request, result)

        future = manifest["future"]
        # Valeurs par défaut du dataclass.
        assert future["product_render_ir_attempted"] is False
        assert future["product_render_extraction_status"] is None
        assert future["product_render_extraction_reason"] is None
