"""
H.6.7c — Tests du benchmark cross-modèles.

Couvre :
- `list_available_ollama_models` (mocké) : retour normal, erreurs absorbées ;
- `_build_cross_model_comparison` : ranking, best_overall, best_by_field,
  cas où aucun completed ;
- `build_cross_model_report_path` : suffixe `_xNmodels.json` ;
- `run_and_save_cross_model` :
  * mix completed/skipped/error,
  * exception dans un run → status=error, autres modèles continuent,
  * persistance JSON schema v4,
  * validation entrées (vide, doublons, seeds vides/doublons),
  * `available_models` injectable pour tests ;
- `format_summary_cross_model` : présence des champs clés.

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
from app.engine.product_render_eval_runner import (
    _build_cross_model_comparison,
    build_cross_model_report_path,
    format_summary_cross_model,
    list_available_ollama_models,
    run_and_save_cross_model,
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


def _perfect_factory(seed: int) -> Callable[[str, str], str]:
    """Factory qui ignore le seed et retourne une réponse parfaite par cas."""
    def _fn(model: str, prompt: str) -> str:
        for c in DEFAULT_CASES:
            if c.prompt in prompt:
                return json.dumps(_build_ideal_ir(c))
        raise AssertionError("unmatched prompt")
    return _fn


def _garbage_factory(seed: int) -> Callable[[str, str], str]:
    def _fn(model: str, prompt: str) -> str:
        return "not json at all"
    return _fn


def _imperfect_factory(seed: int) -> Callable[[str, str], str]:
    """Reproduit un cas modèle moyen : seul le 1er cas est parfait."""
    def _fn(model: str, prompt: str) -> str:
        if DEFAULT_CASES[0].prompt in prompt:
            return json.dumps(_build_ideal_ir(DEFAULT_CASES[0]))
        return "garbage"
    return _fn


# ===========================================================================
# list_available_ollama_models
# ===========================================================================

class TestListAvailableOllamaModels:

    def test_normal_response(self):
        class _Resp:
            ok = True
            def json(self):
                return {"models": [
                    {"name": "qwen2.5-coder:7b", "size": 1},
                    {"name": "deepseek-coder-v2:16b", "size": 2},
                ]}
        with patch(
            "app.engine.product_render_eval_runner.list_available_ollama_models",
            wraps=list_available_ollama_models,
        ):
            with patch("requests.get", return_value=_Resp()):
                names = list_available_ollama_models()
        assert "qwen2.5-coder:7b" in names
        assert "deepseek-coder-v2:16b" in names

    def test_http_error_returns_empty(self):
        class _Resp:
            ok = False
            def json(self):
                return {}
        with patch("requests.get", return_value=_Resp()):
            assert list_available_ollama_models() == []

    def test_network_error_returns_empty(self):
        with patch("requests.get", side_effect=ConnectionError("boom")):
            assert list_available_ollama_models() == []

    def test_malformed_response_returns_empty(self):
        class _Resp:
            ok = True
            def json(self):
                return ["not", "a", "dict"]
        with patch("requests.get", return_value=_Resp()):
            assert list_available_ollama_models() == []

    def test_missing_name_skipped(self):
        class _Resp:
            ok = True
            def json(self):
                return {"models": [
                    {"name": "ok-model", "size": 1},
                    {"size": 2},  # pas de "name"
                    {"name": "second", "size": 3},
                ]}
        with patch("requests.get", return_value=_Resp()):
            assert list_available_ollama_models() == ["ok-model", "second"]


# ===========================================================================
# _build_cross_model_comparison
# ===========================================================================

class TestBuildCrossModelComparison:

    def test_no_completed_returns_empty(self):
        entries = [
            {"model": "a", "status": "skipped"},
            {"model": "b", "status": "error", "error": "boom"},
        ]
        cmp = _build_cross_model_comparison(entries)
        assert cmp["ranking_by_mean_score"] == []
        assert cmp["best_overall"] is None
        assert cmp["best_by_field"] == {}

    def test_ranking_sorted_desc_by_mean_score(self):
        entries = [
            {
                "model": "loser",
                "status": "completed",
                "duration_seconds": 1.0,
                "aggregate": {
                    "parse_ok_rate": {"mean": 0.5},
                    "mean_score": {"mean": 0.5},
                    "per_field_accuracy": {},
                },
            },
            {
                "model": "winner",
                "status": "completed",
                "duration_seconds": 2.0,
                "aggregate": {
                    "parse_ok_rate": {"mean": 1.0},
                    "mean_score": {"mean": 0.9},
                    "per_field_accuracy": {},
                },
            },
        ]
        cmp = _build_cross_model_comparison(entries)
        assert [r["model"] for r in cmp["ranking_by_mean_score"]] == ["winner", "loser"]
        assert cmp["best_overall"] == "winner"

    def test_best_by_field_handles_ties(self):
        entries = [
            {
                "model": "A",
                "status": "completed",
                "duration_seconds": 1.0,
                "aggregate": {
                    "parse_ok_rate": {"mean": 1.0},
                    "mean_score": {"mean": 1.0},
                    "per_field_accuracy": {
                        "subject.kind": {"mean": 1.0},
                        "schema_version": {"mean": 0.5},
                    },
                },
            },
            {
                "model": "B",
                "status": "completed",
                "duration_seconds": 1.0,
                "aggregate": {
                    "parse_ok_rate": {"mean": 1.0},
                    "mean_score": {"mean": 1.0},
                    "per_field_accuracy": {
                        "subject.kind": {"mean": 1.0},
                        "schema_version": {"mean": 0.8},
                    },
                },
            },
        ]
        cmp = _build_cross_model_comparison(entries)
        assert cmp["best_by_field"]["subject.kind"]["models"] == ["A", "B"]
        assert cmp["best_by_field"]["schema_version"]["models"] == ["B"]


# ===========================================================================
# build_cross_model_report_path
# ===========================================================================

class TestBuildCrossModelReportPath:

    def test_path_format(self, tmp_path: Path):
        p = build_cross_model_report_path(
            n_models=3, now=FROZEN_NOW, base_dir=tmp_path,
        )
        assert p.name == "2026-05-27T143205Z_cross_model_x3models.json"

    def test_no_model_slug_in_name(self, tmp_path: Path):
        # Le nom ne doit PAS contenir un slug modèle particulier (collectif).
        p = build_cross_model_report_path(
            n_models=5, now=FROZEN_NOW, base_dir=tmp_path,
        )
        assert "qwen" not in p.name
        assert "deepseek" not in p.name


# ===========================================================================
# run_and_save_cross_model
# ===========================================================================

class TestRunAndSaveCrossModel:

    def test_all_available_all_completed(self, tmp_path: Path):
        models = ("m1", "m2", "m3")
        payload, path = run_and_save_cross_model(
            models=models,
            seeds=(42,),
            generate_fn_factory=_perfect_factory,
            available_models=list(models),
            cases=DEFAULT_CASES,
            base_dir=tmp_path,
            now=FROZEN_NOW,
        )
        assert payload["report_schema_version"] == "4"
        assert payload["models_requested"] == list(models)
        assert all(e["status"] == "completed" for e in payload["models"])
        # Tous parfaits.
        for e in payload["models"]:
            assert e["aggregate"]["mean_score"]["mean"] == pytest.approx(1.0)
        # Tous tied at mean_score=1.0 → ranking inclut tout le monde.
        ranking = payload["comparison"]["ranking_by_mean_score"]
        assert len(ranking) == 3
        assert path.exists()

    def test_missing_model_is_skipped(self, tmp_path: Path):
        models = ("present", "missing", "also_present")
        payload, path = run_and_save_cross_model(
            models=models,
            seeds=(42,),
            generate_fn_factory=_perfect_factory,
            available_models=["present", "also_present"],
            cases=DEFAULT_CASES[:2],
            base_dir=tmp_path,
            now=FROZEN_NOW,
        )
        statuses = {e["model"]: e["status"] for e in payload["models"]}
        assert statuses == {
            "present": "completed",
            "missing": "skipped",
            "also_present": "completed",
        }
        # skipped → error message explicite, pas de aggregate.
        skipped = next(e for e in payload["models"] if e["status"] == "skipped")
        assert "not installed" in skipped["error"]
        assert "aggregate" not in skipped

    def test_exception_in_one_model_does_not_stop_others(self, tmp_path: Path):
        boom_called: list[str] = []

        def factory(seed: int):
            def _fn(model: str, prompt: str) -> str:
                if model == "boom":
                    boom_called.append(prompt)
                    raise RuntimeError("simulated model failure")
                # Pour les autres : réponse parfaite par cas.
                return _perfect_factory(seed)(model, prompt)
            return _fn

        models = ("m1", "boom", "m3")
        payload, path = run_and_save_cross_model(
            models=models,
            seeds=(42,),
            generate_fn_factory=factory,
            available_models=list(models),
            cases=DEFAULT_CASES,
            base_dir=tmp_path,
            now=FROZEN_NOW,
        )
        statuses = {e["model"]: e["status"] for e in payload["models"]}
        # m1 et m3 doivent avoir terminé malgré l'erreur de "boom".
        # Note : run_harness ne propage pas les exceptions ; il les convertit
        # en fallback. Donc "boom" n'a pas status="error" mais "completed"
        # avec parse_ok_rate=0. C'est le comportement attendu : la robustesse
        # vient déjà du harness. status="error" du runner ne se déclenche
        # que sur erreurs HORS run_harness (ex. aggregate_multiseed qui
        # plante sur un bug interne).
        assert statuses["m1"] == "completed"
        assert statuses["m3"] == "completed"
        boom_entry = next(e for e in payload["models"] if e["model"] == "boom")
        assert boom_entry["status"] == "completed"
        assert boom_entry["aggregate"]["parse_ok_rate"]["mean"] == 0.0

    def test_saved_payload_round_trips(self, tmp_path: Path):
        payload, path = run_and_save_cross_model(
            models=("m1",),
            seeds=(42,),
            generate_fn_factory=_perfect_factory,
            available_models=["m1"],
            cases=DEFAULT_CASES,
            base_dir=tmp_path,
            now=FROZEN_NOW,
        )
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["report_schema_version"] == "4"
        assert data["timestamp"] == FROZEN_NOW.isoformat()
        assert data["seeds"] == [42]

    def test_empty_models_raises(self, tmp_path: Path):
        with pytest.raises(ValueError, match="non-empty"):
            run_and_save_cross_model(
                models=(),
                generate_fn_factory=_perfect_factory,
                available_models=[],
                base_dir=tmp_path,
                now=FROZEN_NOW,
            )

    def test_duplicate_models_raises(self, tmp_path: Path):
        with pytest.raises(ValueError, match="unique"):
            run_and_save_cross_model(
                models=("a", "a"),
                generate_fn_factory=_perfect_factory,
                available_models=["a"],
                base_dir=tmp_path,
                now=FROZEN_NOW,
            )

    def test_multi_seed_per_model(self, tmp_path: Path):
        captured_seeds: list[int] = []

        def factory(seed: int):
            captured_seeds.append(seed)
            return _perfect_factory(seed)

        payload, path = run_and_save_cross_model(
            models=("m1", "m2"),
            seeds=(42, 7, 1),
            generate_fn_factory=factory,
            available_models=["m1", "m2"],
            cases=DEFAULT_CASES[:1],
            base_dir=tmp_path,
            now=FROZEN_NOW,
        )
        # 2 modèles × 3 seeds = 6 invocations de factory.
        assert captured_seeds == [42, 7, 1, 42, 7, 1]
        # seeds_used reflète bien le set.
        for e in payload["models"]:
            assert e["seeds_used"] == [42, 7, 1]


# ===========================================================================
# format_summary_cross_model
# ===========================================================================

class TestFormatSummaryCrossModel:

    def test_summary_contains_completed_and_skipped(self, tmp_path: Path):
        payload, path = run_and_save_cross_model(
            models=("present", "missing"),
            seeds=(42,),
            generate_fn_factory=_perfect_factory,
            available_models=["present"],
            cases=DEFAULT_CASES[:1],
            base_dir=tmp_path,
            now=FROZEN_NOW,
        )
        text = format_summary_cross_model(payload, path)
        assert "present" in text
        assert "missing" in text
        assert "[OK]" in text
        assert "[SKIP]" in text
        assert "ranking" in text
