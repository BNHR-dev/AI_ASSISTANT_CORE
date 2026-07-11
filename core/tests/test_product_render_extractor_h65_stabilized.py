"""
H.6.5.a — Tests du wrapper d'extraction stabilisé.

Vérifie que `extract_product_render_intent` sans `generate_fn` injecté passe
bien par `_default_extraction_generate_fn`, et que ce dernier appelle
`generate_with_ollama` avec les paramètres d'inférence + format attendus.

Aucun appel Ollama réel.
"""
from __future__ import annotations

from unittest.mock import patch


from app.engine import product_render_extractor as ext
from app.engine.product_render_extractor import (
    EXTRACTION_INFERENCE_OPTIONS,
    EXTRACTION_RESPONSE_FORMAT,
    _default_extraction_generate_fn,
    extract_product_render_intent,
)


# ---------------------------------------------------------------------------
# Sanity sur les constantes (preuve qu'elles existent et sont raisonnables)
# ---------------------------------------------------------------------------

class TestInferenceConstants:

    def test_format_is_json(self):
        assert EXTRACTION_RESPONSE_FORMAT == "json"

    def test_temperature_zero(self):
        assert EXTRACTION_INFERENCE_OPTIONS["temperature"] == 0.0

    def test_seed_is_fixed(self):
        # Tout entier convient ; on vérifie juste qu'un seed est défini.
        assert isinstance(EXTRACTION_INFERENCE_OPTIONS["seed"], int)

    def test_num_ctx_is_reasonable(self):
        # 4096 couvre largement le prompt actuel (~1-2k tokens).
        assert EXTRACTION_INFERENCE_OPTIONS["num_ctx"] >= 4096


# ---------------------------------------------------------------------------
# Wrapper _default_extraction_generate_fn
# ---------------------------------------------------------------------------

class TestDefaultExtractionGenerateFn:

    def test_passes_model_prompt_options_format(self):
        with patch(
            "app.engine.product_render_extractor.generate_with_ollama",
            return_value="{}",
        ) as mocked:
            out = _default_extraction_generate_fn("model-x", "prompt-y")
        assert out == "{}"
        mocked.assert_called_once()
        args, kwargs = mocked.call_args
        assert args == ("model-x", "prompt-y")
        assert kwargs["options"] == EXTRACTION_INFERENCE_OPTIONS
        assert kwargs["format"] == EXTRACTION_RESPONSE_FORMAT

    def test_returns_generate_with_ollama_output_verbatim(self):
        with patch(
            "app.engine.product_render_extractor.generate_with_ollama",
            return_value="some raw output",
        ):
            assert _default_extraction_generate_fn("m", "p") == "some raw output"


# ---------------------------------------------------------------------------
# Intégration : extract_product_render_intent sans generate_fn injecté
# ---------------------------------------------------------------------------

class TestExtractorUsesStabilizedDefaults:

    def test_extract_without_generate_fn_uses_stabilized_wrapper(self):
        # On mocke `generate_with_ollama` au point d'import de l'extracteur :
        # si le wrapper par défaut est correctement câblé, le mock reçoit
        # les options/format stabilisés.
        with patch(
            "app.engine.product_render_extractor.generate_with_ollama",
            return_value='{"schema_version":"v0","subject":{"kind":"bottle","color":"amber","material":"glass"},"backdrop":{"color":"neutral_gray"}}',
        ) as mocked:
            result = extract_product_render_intent("bouteille ambrée fond gris")

        assert result.status == "parsed"
        # Le wrapper stabilisé a été utilisé → options + format propagés.
        assert mocked.called
        kwargs = mocked.call_args.kwargs
        assert kwargs.get("options") == EXTRACTION_INFERENCE_OPTIONS
        assert kwargs.get("format") == EXTRACTION_RESPONSE_FORMAT

    def test_extract_with_injected_generate_fn_bypasses_stabilized_wrapper(self):
        # Si l'utilisateur fournit son propre generate_fn (cas typique des
        # tests existants), le wrapper stabilisé N'EST PAS appelé.
        captured: dict = {}

        def fake_gen(model: str, prompt: str) -> str:
            captured["model"] = model
            captured["prompt_len"] = len(prompt)
            return '{"schema_version":"v0","subject":{"kind":"bottle","color":"amber","material":"glass"},"backdrop":{"color":"neutral_gray"}}'

        with patch(
            "app.engine.product_render_extractor.generate_with_ollama",
        ) as mocked:
            result = extract_product_render_intent("x", generate_fn=fake_gen)

        assert result.status == "parsed"
        # `generate_with_ollama` ne doit PAS avoir été appelé.
        assert mocked.call_count == 0
        # Le mock injecté a été utilisé.
        assert captured["model"] == ext.DEFAULT_EXTRACTION_MODEL

    def test_llm_call_exception_still_yields_fallback_under_default_wrapper(self):
        # Si Ollama lève (réseau, modèle introuvable, ...), le wrapper
        # propage l'exception ; l'extractor doit la convertir en fallback.
        with patch(
            "app.engine.product_render_extractor.generate_with_ollama",
            side_effect=RuntimeError("Ollama error 500"),
        ):
            result = extract_product_render_intent("anything")
        assert result.status == "fallback"
        assert "llm_call_error" in (result.error or "")
        assert "RuntimeError" in (result.error or "")
