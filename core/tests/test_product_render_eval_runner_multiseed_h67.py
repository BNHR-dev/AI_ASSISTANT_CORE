"""
H.6.7a — Tests du benchmark multi-seed.

Couvre :
- `aggregate_multiseed` : agrégation correcte, validation longueurs,
  cohérence corpus, cas vide ;
- `build_multiseed_report_path` : format `_x{N}seeds.json` ;
- `run_and_save_multiseed` : factory custom utilisée pour chaque seed,
  rapport sauvegardé en JSON schema "3", erreurs sur entrées invalides ;
- `format_summary_multiseed` : présence des champs clés ;
- intégration : factory par défaut (sans Ollama réel : monkeypatch).

Aucun appel Ollama réel.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from unittest.mock import patch

import pytest

from app.engine.product_render_eval_cases import DEFAULT_CASES, EvalCase
from app.engine.product_render_eval_harness import run_harness
from app.engine.product_render_eval_runner import (
    DEFAULT_SEEDS,
    aggregate_multiseed,
    build_multiseed_report_path,
    format_summary_multiseed,
    run_and_save_multiseed,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FROZEN_NOW = datetime(2026, 5, 27, 14, 32, 5, tzinfo=timezone.utc)


def _build_ideal_ir(case: EvalCase) -> dict:
    sv = case.expected.get("schema_version", "v0")
    ir: dict = {"schema_version": sv}
    subject: dict = {
        "kind": case.expected.get("subject.kind", "bottle"),
        "color": case.expected.get("subject.color", "amber"),
        "material": case.expected.get("subject.material", "glass"),
    }
    if sv == "v1":
        for k_full, k_short in [
            ("subject.shape", "shape"),
            ("subject.cap", "cap"),
            ("subject.transparency", "transparency"),
        ]:
            if k_full in case.expected:
                subject[k_short] = case.expected[k_full]
    # semantic_fidelity_v1 — kind_fidelity est version-neutre.
    if "subject.kind_fidelity" in case.expected:
        subject["kind_fidelity"] = case.expected["subject.kind_fidelity"]
    ir["subject"] = subject
    ir["backdrop"] = {"color": case.expected.get("backdrop.color", "neutral_gray")}
    if sv == "v1" and "framing" in case.expected:
        ir["framing"] = case.expected["framing"]
    if sv == "v1" and "pedestal.color" in case.expected:
        ir["pedestal"] = {"color": case.expected["pedestal.color"]}
        if "pedestal.material" in case.expected:
            ir["pedestal"]["material"] = case.expected["pedestal.material"]
    return ir


def _mock_gen(responses_by_prompt: dict[str, str]) -> Callable[[str, str], str]:
    def _fn(model: str, prompt: str) -> str:
        for needle, resp in responses_by_prompt.items():
            if needle in prompt:
                return resp
        raise AssertionError(f"no mock response matched: {prompt[:80]}")
    return _fn


def _perfect_gen() -> Callable[[str, str], str]:
    return _mock_gen({
        c.prompt: json.dumps(_build_ideal_ir(c)) for c in DEFAULT_CASES
    })


def _garbage_gen() -> Callable[[str, str], str]:
    return _mock_gen({c.prompt: "not json" for c in DEFAULT_CASES})


# ===========================================================================
# DEFAULT_SEEDS
# ===========================================================================

class TestDefaultSeeds:

    def test_default_seeds_include_42(self):
        # Traçabilité avec baseline H.6.6 (qui utilisait seed=42 figé).
        assert 42 in DEFAULT_SEEDS

    def test_default_seeds_are_unique(self):
        assert len(set(DEFAULT_SEEDS)) == len(DEFAULT_SEEDS)

    def test_default_seeds_count(self):
        assert len(DEFAULT_SEEDS) == 5


# ===========================================================================
# aggregate_multiseed
# ===========================================================================

class TestAggregateMultiseed:

    def test_empty(self):
        out = aggregate_multiseed([], [])
        assert out["n_seeds"] == 0
        assert out["seeds"] == []
        assert out["aggregate"]["parse_ok_rate"]["mean"] == 0.0

    def test_length_mismatch_raises(self):
        gen = _perfect_gen()
        r = run_harness(generate_fn=gen, model="m", cases=DEFAULT_CASES[:1])
        with pytest.raises(ValueError, match="len\\(seeds\\)"):
            aggregate_multiseed([1, 2], [r])

    def test_seeds_recorded(self):
        gen = _perfect_gen()
        reports = [
            run_harness(generate_fn=gen, model="m", cases=DEFAULT_CASES)
            for _ in range(3)
        ]
        out = aggregate_multiseed([42, 7, 1], reports)
        assert out["seeds"] == [42, 7, 1]
        assert out["n_seeds"] == 3
        # per_seed_summaries indexé par seed (pas par position).
        assert [s["seed"] for s in out["per_seed_summaries"]] == [42, 7, 1]

    def test_identical_reports_zero_stdev(self):
        gen = _perfect_gen()
        reports = [
            run_harness(generate_fn=gen, model="m", cases=DEFAULT_CASES)
            for _ in range(3)
        ]
        out = aggregate_multiseed([1, 2, 3], reports)
        assert out["aggregate"]["mean_score"]["stdev"] == pytest.approx(0.0)
        assert out["aggregate"]["parse_ok_rate"]["stdev"] == pytest.approx(0.0)

    def test_varying_reports_nonzero_stdev(self):
        r1 = run_harness(generate_fn=_perfect_gen(), model="m", cases=DEFAULT_CASES)
        r2 = run_harness(generate_fn=_garbage_gen(), model="m", cases=DEFAULT_CASES)
        out = aggregate_multiseed([42, 7], [r1, r2])
        assert out["aggregate"]["parse_ok_rate"]["mean"] == pytest.approx(0.5)
        assert out["aggregate"]["parse_ok_rate"]["stdev"] == pytest.approx(0.5)

    def test_inconsistent_corpus_raises(self):
        gen = _perfect_gen()
        r_full = run_harness(generate_fn=gen, model="m", cases=DEFAULT_CASES)
        r_short = run_harness(generate_fn=gen, model="m", cases=DEFAULT_CASES[:3])
        with pytest.raises(ValueError, match="total_cases varie"):
            aggregate_multiseed([1, 2], [r_full, r_short])

    def test_case_aggregates_ordered_as_corpus(self):
        gen = _perfect_gen()
        reports = [
            run_harness(generate_fn=gen, model="m", cases=DEFAULT_CASES)
            for _ in range(2)
        ]
        out = aggregate_multiseed([1, 2], reports)
        assert [c["case_id"] for c in out["case_aggregates"]] == \
               [c.id for c in DEFAULT_CASES]


# ===========================================================================
# build_multiseed_report_path
# ===========================================================================

class TestBuildMultiseedReportPath:

    def test_path_format(self, tmp_path: Path):
        p = build_multiseed_report_path(
            model="qwen2.5-coder:7b",
            n_seeds=5,
            now=FROZEN_NOW,
            base_dir=tmp_path,
        )
        assert p.name == "2026-05-27T143205Z_qwen2.5-coder-7b_x5seeds.json"

    def test_distinct_from_multirun_path(self, tmp_path: Path):
        from app.engine.product_render_eval_runner import build_multirun_report_path
        a = build_multirun_report_path(
            model="m", n_runs=5, now=FROZEN_NOW, base_dir=tmp_path,
        )
        b = build_multiseed_report_path(
            model="m", n_seeds=5, now=FROZEN_NOW, base_dir=tmp_path,
        )
        assert a != b
        assert "runs" in a.name
        assert "seeds" in b.name


# ===========================================================================
# run_and_save_multiseed
# ===========================================================================

class TestRunAndSaveMultiseed:

    def test_factory_called_per_seed_with_correct_value(self, tmp_path: Path):
        captured: list[int] = []

        def factory(seed: int):
            captured.append(seed)
            return _perfect_gen()

        seeds = (1, 7, 42)
        payload, path = run_and_save_multiseed(
            seeds=seeds,
            generate_fn_factory=factory,
            model="m",
            cases=DEFAULT_CASES[:2],
            base_dir=tmp_path,
            now=FROZEN_NOW,
        )
        assert tuple(captured) == seeds
        assert payload["seeds"] == [1, 7, 42]
        assert payload["report_schema_version"] == "3"

    def test_saved_report_round_trips(self, tmp_path: Path):
        payload, path = run_and_save_multiseed(
            seeds=(1, 2, 3),
            generate_fn_factory=lambda s: _perfect_gen(),
            model="m",
            cases=DEFAULT_CASES,
            base_dir=tmp_path,
            now=FROZEN_NOW,
        )
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["report_schema_version"] == "3"
        assert data["timestamp"] == FROZEN_NOW.isoformat()
        assert data["seeds"] == [1, 2, 3]
        assert data["n_seeds"] == 3
        assert data["aggregate"]["parse_ok_rate"]["mean"] == 1.0

    def test_empty_seeds_raises(self, tmp_path: Path):
        with pytest.raises(ValueError, match="non-empty"):
            run_and_save_multiseed(
                seeds=(),
                generate_fn_factory=lambda s: _perfect_gen(),
                model="m",
                base_dir=tmp_path,
                now=FROZEN_NOW,
            )

    def test_duplicate_seeds_raises(self, tmp_path: Path):
        with pytest.raises(ValueError, match="unique"):
            run_and_save_multiseed(
                seeds=(1, 1, 2),
                generate_fn_factory=lambda s: _perfect_gen(),
                model="m",
                base_dir=tmp_path,
                now=FROZEN_NOW,
            )

    def test_default_factory_uses_build_extraction_generate_fn(
        self, tmp_path: Path,
    ):
        # Sanity : sans `generate_fn_factory` injecté, le runner doit utiliser
        # `build_extraction_generate_fn` qui passe par `generate_with_ollama`.
        # On monkeypatche `generate_with_ollama` au point d'import de l'extractor
        # pour intercepter sans toucher au réseau.
        captured_seeds: list[int] = []

        def fake_gen_with_ollama(model, prompt, *, options=None, format=None):
            if options:
                captured_seeds.append(options["seed"])
            return json.dumps(_build_ideal_ir(DEFAULT_CASES[0]))

        with patch(
            "app.engine.product_render_extractor.generate_with_ollama",
            side_effect=fake_gen_with_ollama,
        ):
            payload, path = run_and_save_multiseed(
                seeds=(11, 22, 33),
                model="m",
                cases=DEFAULT_CASES[:1],
                base_dir=tmp_path,
                now=FROZEN_NOW,
            )
        assert captured_seeds == [11, 22, 33]
        assert payload["seeds"] == [11, 22, 33]


# ===========================================================================
# format_summary_multiseed
# ===========================================================================

class TestFormatSummaryMultiseed:

    def test_summary_contains_key_fields(self, tmp_path: Path):
        payload, path = run_and_save_multiseed(
            seeds=(1, 2),
            generate_fn_factory=lambda s: _perfect_gen(),
            model="m",
            cases=DEFAULT_CASES[:2],
            base_dir=tmp_path,
            now=FROZEN_NOW,
        )
        text = format_summary_multiseed(payload, path)
        assert "seeds" in text
        assert "parse_ok_rate" in text
        assert "mean_score" in text
        assert "per_field_accuracy" in text
        assert "case_aggregates" in text
        assert "per_seed_summaries" in text
        # Les valeurs de seed apparaissent.
        assert "seed=1" in text
        assert "seed=2" in text
