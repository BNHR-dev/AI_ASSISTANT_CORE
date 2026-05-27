"""
H.6.5.b — Tests du benchmark multi-run.

Couvre :
- `_stats_block` (pure) sur listes vides/N=1/N>1 ;
- `aggregate_multirun` :
  * cas vide,
  * runs identiques → stdev=0 sur toutes les métriques,
  * runs variés → moyennes et stdev cohérents,
  * cohérence du `case_id` cross-run ;
- `build_multirun_report_path` (suffixe _xN.json) ;
- `run_and_save_multi` (orchestration + persistance + schema v2).

Aucun appel Ollama réel : `generate_fn` est mocké.
"""
from __future__ import annotations

import json
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import pytest

from app.engine.product_render_eval_cases import DEFAULT_CASES, EvalCase
from app.engine.product_render_eval_harness import HarnessReport, run_harness
from app.engine.product_render_eval_runner import (
    _stats_block,
    aggregate_multirun,
    build_multirun_report_path,
    format_summary_multirun,
    run_and_save_multi,
    slugify_model,
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
    ir["subject"] = subject
    ir["backdrop"] = {"color": case.expected.get("backdrop.color", "neutral_gray")}
    if sv == "v1" and "framing" in case.expected:
        ir["framing"] = case.expected["framing"]
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
# _stats_block
# ===========================================================================

class TestStatsBlock:

    def test_empty(self):
        assert _stats_block([]) == {"mean": 0.0, "min": 0.0, "max": 0.0, "stdev": 0.0}

    def test_single_value(self):
        s = _stats_block([0.5])
        assert s["mean"] == 0.5
        assert s["min"] == 0.5
        assert s["max"] == 0.5
        assert s["stdev"] == 0.0   # pstdev sur N=1 = 0

    def test_multiple_values(self):
        s = _stats_block([0.0, 1.0])
        assert s["mean"] == 0.5
        assert s["min"] == 0.0
        assert s["max"] == 1.0
        # pstdev de [0, 1] = 0.5 exactement.
        assert s["stdev"] == pytest.approx(0.5)


# ===========================================================================
# aggregate_multirun
# ===========================================================================

class TestAggregateMultirun:

    def test_empty_reports(self):
        out = aggregate_multirun([])
        assert out["n_runs"] == 0
        assert out["n_cases"] == 0
        assert out["aggregate"]["parse_ok_rate"]["mean"] == 0.0
        assert out["case_aggregates"] == []

    def test_identical_runs_have_zero_stdev(self):
        # 3 fois le même rapport "parfait".
        gen = _perfect_gen()
        reports = [
            run_harness(generate_fn=gen, model="m", cases=DEFAULT_CASES)
            for _ in range(3)
        ]
        out = aggregate_multirun(reports)
        assert out["n_runs"] == 3
        assert out["n_cases"] == len(DEFAULT_CASES)
        agg = out["aggregate"]
        assert agg["parse_ok_rate"]["mean"] == pytest.approx(1.0)
        assert agg["parse_ok_rate"]["stdev"] == pytest.approx(0.0)
        assert agg["mean_score"]["mean"] == pytest.approx(1.0)
        assert agg["mean_score"]["stdev"] == pytest.approx(0.0)
        for stats in agg["per_field_accuracy"].values():
            assert stats["mean"] == pytest.approx(1.0)
            assert stats["stdev"] == pytest.approx(0.0)
        # Tous les cas : parse_ok_count == 3.
        for c in out["case_aggregates"]:
            assert c["parse_ok_count"] == 3
            assert c["score"]["stdev"] == pytest.approx(0.0)

    def test_varying_runs_produce_nonzero_stdev(self):
        # 1 run parfait + 1 run garbage.
        r1 = run_harness(generate_fn=_perfect_gen(), model="m", cases=DEFAULT_CASES)
        r2 = run_harness(generate_fn=_garbage_gen(), model="m", cases=DEFAULT_CASES)
        out = aggregate_multirun([r1, r2])
        agg = out["aggregate"]
        # parse_ok_rate alterne entre 1.0 et 0.0 → mean=0.5, stdev=0.5.
        assert agg["parse_ok_rate"]["mean"] == pytest.approx(0.5)
        assert agg["parse_ok_rate"]["stdev"] == pytest.approx(0.5)
        assert agg["mean_score"]["mean"] == pytest.approx(0.5)
        assert agg["mean_score"]["stdev"] == pytest.approx(0.5)

    def test_per_run_summaries_present_with_case_results(self):
        gen = _perfect_gen()
        reports = [run_harness(generate_fn=gen, model="m", cases=DEFAULT_CASES[:2])
                   for _ in range(2)]
        out = aggregate_multirun(reports)
        assert len(out["per_run_summaries"]) == 2
        for prs in out["per_run_summaries"]:
            assert prs["run_index"] in (0, 1)
            assert prs["parse_ok_rate"] == 1.0
            assert len(prs["case_results"]) == 2

    def test_common_errors_counted(self):
        # Garbage → tous les cas en fallback avec "no_json_block_found"
        # (le parser ne trouve pas de JSON dans "not json").
        gen = _garbage_gen()
        reports = [run_harness(generate_fn=gen, model="m", cases=DEFAULT_CASES[:3])
                   for _ in range(2)]
        out = aggregate_multirun(reports)
        assert out["common_errors"]
        top = out["common_errors"][0]
        assert top["error_prefix"] == "no_json_block_found"
        assert top["count"] == 6  # 3 cas × 2 runs

    def test_case_aggregates_order_matches_corpus(self):
        gen = _perfect_gen()
        reports = [run_harness(generate_fn=gen, model="m", cases=DEFAULT_CASES)
                   for _ in range(2)]
        out = aggregate_multirun(reports)
        assert [c["case_id"] for c in out["case_aggregates"]] == \
               [c.id for c in DEFAULT_CASES]

    def test_inconsistent_total_cases_raises(self):
        gen = _perfect_gen()
        r_full = run_harness(generate_fn=gen, model="m", cases=DEFAULT_CASES)
        r_short = run_harness(generate_fn=gen, model="m", cases=DEFAULT_CASES[:3])
        with pytest.raises(ValueError, match="total_cases varie"):
            aggregate_multirun([r_full, r_short])


# ===========================================================================
# build_multirun_report_path
# ===========================================================================

class TestBuildMultirunReportPath:

    def test_suffix_includes_n_runs(self, tmp_path: Path):
        p = build_multirun_report_path(
            model="qwen2.5-coder:7b",
            n_runs=5,
            now=FROZEN_NOW,
            base_dir=tmp_path,
        )
        assert p.name == "2026-05-27T143205Z_qwen2.5-coder-7b_x5runs.json"

    def test_distinct_from_singlerun_path(self, tmp_path: Path):
        from app.engine.product_render_eval_runner import build_report_path
        single = build_report_path(
            model="qwen2.5-coder:7b", now=FROZEN_NOW, base_dir=tmp_path,
        )
        multi = build_multirun_report_path(
            model="qwen2.5-coder:7b", n_runs=5, now=FROZEN_NOW, base_dir=tmp_path,
        )
        assert single != multi


# ===========================================================================
# run_and_save_multi
# ===========================================================================

class TestRunAndSaveMulti:

    def test_n_equals_1_still_produces_multirun_format(self, tmp_path: Path):
        payload, path = run_and_save_multi(
            n_runs=1,
            generate_fn=_perfect_gen(),
            model="m",
            cases=DEFAULT_CASES[:2],
            base_dir=tmp_path,
            now=FROZEN_NOW,
        )
        assert payload["report_schema_version"] == "2"
        assert payload["n_runs"] == 1
        assert payload["aggregate"]["mean_score"]["stdev"] == 0.0
        assert path.exists()

    def test_n_equals_5_runs_harness_5_times(self, tmp_path: Path):
        call_count = {"n": 0}

        def counting_gen(model: str, prompt: str) -> str:
            call_count["n"] += 1
            # Renvoie une réponse parfaite pour le 1er cas.
            return json.dumps(_build_ideal_ir(DEFAULT_CASES[0]))

        payload, path = run_and_save_multi(
            n_runs=5,
            generate_fn=counting_gen,
            model="m",
            cases=DEFAULT_CASES[:1],
            base_dir=tmp_path,
            now=FROZEN_NOW,
        )
        assert payload["n_runs"] == 5
        # 1 cas × 5 runs = 5 appels generate_fn.
        assert call_count["n"] == 5

    def test_saved_report_is_valid_json(self, tmp_path: Path):
        payload, path = run_and_save_multi(
            n_runs=3,
            generate_fn=_perfect_gen(),
            model="m",
            cases=DEFAULT_CASES,
            base_dir=tmp_path,
            now=FROZEN_NOW,
        )
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["report_schema_version"] == "2"
        assert data["timestamp"] == FROZEN_NOW.isoformat()
        assert data["n_runs"] == 3
        assert data["aggregate"]["parse_ok_rate"]["mean"] == 1.0

    def test_invalid_n_runs_raises(self, tmp_path: Path):
        with pytest.raises(ValueError, match="n_runs"):
            run_and_save_multi(
                n_runs=0,
                generate_fn=_perfect_gen(),
                model="m",
                base_dir=tmp_path,
                now=FROZEN_NOW,
            )

    def test_default_model_from_env(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ):
        monkeypatch.setenv("AAC_BLENDER_LLM_MODEL", "envmodel:7b")
        payload, path = run_and_save_multi(
            n_runs=1,
            generate_fn=_mock_gen({c.prompt: "" for c in DEFAULT_CASES[:1]}),
            cases=DEFAULT_CASES[:1],
            base_dir=tmp_path,
            now=FROZEN_NOW,
        )
        assert payload["model"] == "envmodel:7b"
        assert "envmodel-7b" in path.name


# ===========================================================================
# format_summary_multirun
# ===========================================================================

class TestFormatSummaryMultirun:

    def test_summary_contains_key_metrics(self, tmp_path: Path):
        payload, path = run_and_save_multi(
            n_runs=3,
            generate_fn=_perfect_gen(),
            model="m",
            cases=DEFAULT_CASES[:2],
            base_dir=tmp_path,
            now=FROZEN_NOW,
        )
        text = format_summary_multirun(payload, path)
        assert "n_runs" in text
        assert "parse_ok_rate" in text
        assert "mean_score" in text
        assert "per_field_accuracy" in text
        assert "case_aggregates" in text
        # case_id du 1er cas du corpus apparaît.
        assert DEFAULT_CASES[0].id in text
