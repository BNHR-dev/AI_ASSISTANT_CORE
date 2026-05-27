"""
H.6.7a — Tests de `build_extraction_generate_fn(seed)`.

Vérifie :
- la factory retourne une closure callable `(model, prompt) -> str` ;
- le seed passé est bien transporté dans les options Ollama ;
- les autres paramètres d'inférence (temperature, top_p, top_k, num_ctx)
  sont préservés depuis `EXTRACTION_INFERENCE_OPTIONS` ;
- le format JSON serveur est préservé ;
- l'isolation : modifier les options retournées par la closure ne corrompt
  pas le `EXTRACTION_INFERENCE_OPTIONS` global.

Aucun appel Ollama réel.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.engine.product_render_extractor import (
    EXTRACTION_INFERENCE_OPTIONS,
    EXTRACTION_RESPONSE_FORMAT,
    build_extraction_generate_fn,
)


class TestBuildExtractionGenerateFnFactory:

    def test_returns_callable(self):
        fn = build_extraction_generate_fn(seed=123)
        assert callable(fn)

    def test_seed_is_propagated(self):
        with patch(
            "app.engine.product_render_extractor.generate_with_ollama",
            return_value="{}",
        ) as mocked:
            fn = build_extraction_generate_fn(seed=123)
            fn("model-x", "prompt-y")
        kwargs = mocked.call_args.kwargs
        assert kwargs["options"]["seed"] == 123

    def test_other_inference_options_preserved(self):
        with patch(
            "app.engine.product_render_extractor.generate_with_ollama",
            return_value="{}",
        ) as mocked:
            fn = build_extraction_generate_fn(seed=999)
            fn("m", "p")
        opts = mocked.call_args.kwargs["options"]
        # Tous les paramètres autres que seed sont identiques au global.
        for key, val in EXTRACTION_INFERENCE_OPTIONS.items():
            if key == "seed":
                continue
            assert opts[key] == val, f"{key} divergent"

    def test_response_format_preserved(self):
        with patch(
            "app.engine.product_render_extractor.generate_with_ollama",
            return_value="{}",
        ) as mocked:
            fn = build_extraction_generate_fn(seed=1)
            fn("m", "p")
        assert mocked.call_args.kwargs["format"] == EXTRACTION_RESPONSE_FORMAT

    def test_global_options_not_mutated(self):
        # Sanity : la factory doit travailler sur une COPIE.
        before = dict(EXTRACTION_INFERENCE_OPTIONS)
        with patch(
            "app.engine.product_render_extractor.generate_with_ollama",
            return_value="{}",
        ):
            fn = build_extraction_generate_fn(seed=12345)
            fn("m", "p")
        assert dict(EXTRACTION_INFERENCE_OPTIONS) == before
        # Le seed global reste 42 (figé H.6.5.a).
        assert EXTRACTION_INFERENCE_OPTIONS["seed"] == 42

    @pytest.mark.parametrize("seed", [0, 1, 42, 999, 2**31 - 1])
    def test_various_seeds_pass_through(self, seed):
        with patch(
            "app.engine.product_render_extractor.generate_with_ollama",
            return_value="{}",
        ) as mocked:
            fn = build_extraction_generate_fn(seed=seed)
            fn("m", "p")
        assert mocked.call_args.kwargs["options"]["seed"] == seed
