"""
H.6.3 — Tests du runner d'eval persistant.

Scope :
- slugify_model : variantes de noms de modèles Ollama.
- build_report_path : format de chemin stable, tri lexicographique.
- save_report : écriture JSON valide, round-trip, création de répertoire.
- run_and_save : orchestration complète avec generate_fn mocké (sans Ollama).
- format_summary : sortie console déterministe.

Aucune dépendance Ollama / réseau. Le bloc __main__ du runner n'est PAS
testé : il dépend d'Ollama et reste hors CI.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import pytest

from app.engine.product_render_eval_cases import DEFAULT_CASES, EvalCase
from app.engine.product_render_eval_harness import HarnessReport, run_harness
from app.engine.product_render_eval_runner import (
    DEFAULT_EVAL_REPORTS_DIR,
    _enrich_report,
    _format_timestamp,
    build_report_path,
    format_summary,
    run_and_save,
    save_report,
    slugify_model,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FROZEN_NOW = datetime(2026, 5, 27, 14, 32, 5, tzinfo=timezone.utc)


def _build_ideal_ir(case: EvalCase) -> dict:
    """Réplique compacte du helper de test_product_render_eval_harness."""
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
        raise AssertionError(f"no mock response matched prompt: {prompt[:80]}")
    return _fn


# ===========================================================================
# slugify_model
# ===========================================================================

class TestSlugifyModel:

    def test_colon_becomes_dash(self):
        assert slugify_model("qwen2.5-coder:7b") == "qwen2.5-coder-7b"

    def test_slash_becomes_dash(self):
        assert slugify_model("Qwen/Qwen2.5-VL:3B") == "Qwen-Qwen2.5-VL-3B"

    def test_preserves_dots_letters_digits_dashes(self):
        assert slugify_model("foo-1.2.3-bar") == "foo-1.2.3-bar"

    def test_trims_outer_dashes(self):
        assert slugify_model(":model:") == "model"

    def test_collapses_multiple_separators(self):
        assert slugify_model("a / / b") == "a-b"

    def test_empty_string_yields_unknown(self):
        assert slugify_model("") == "unknown-model"
        assert slugify_model("   ") == "unknown-model"

    def test_only_separators_yields_unknown(self):
        assert slugify_model(":::") == "unknown-model"


# ===========================================================================
# build_report_path / format_timestamp
# ===========================================================================

class TestBuildReportPath:

    def test_path_format(self, tmp_path: Path):
        path = build_report_path(
            model="qwen2.5-coder:7b",
            now=FROZEN_NOW,
            base_dir=tmp_path,
        )
        assert path.parent == tmp_path
        assert path.name == "2026-05-27T143205Z_qwen2.5-coder-7b.json"

    def test_timestamp_has_no_colons(self):
        # Important pour Windows : `:` interdit dans les noms de fichier.
        ts = _format_timestamp(FROZEN_NOW)
        assert ":" not in ts
        assert ts == "2026-05-27T143205Z"

    def test_lexicographic_order_matches_chronological(self, tmp_path: Path):
        earlier = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        later = datetime(2026, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
        p_earlier = build_report_path(model="m", now=earlier, base_dir=tmp_path)
        p_later = build_report_path(model="m", now=later, base_dir=tmp_path)
        assert p_earlier.name < p_later.name

    def test_default_base_dir(self):
        # Pas d'I/O, on vérifie juste la valeur par défaut.
        path = build_report_path(model="m", now=FROZEN_NOW)
        assert path.parent == DEFAULT_EVAL_REPORTS_DIR


# ===========================================================================
# _enrich_report
# ===========================================================================

class TestEnrichReport:

    def test_adds_timestamp_and_schema_version(self):
        report = HarnessReport(
            model="m",
            case_scores=(),
            total_cases=0,
            parse_ok_rate=0.0,
            mean_score=0.0,
            per_field_accuracy={},
        )
        enriched = _enrich_report(report, now=FROZEN_NOW)
        assert enriched["report_schema_version"] == "1"
        assert enriched["timestamp"] == FROZEN_NOW.isoformat()
        # Conserve les champs du harness.
        assert enriched["model"] == "m"
        assert enriched["total_cases"] == 0


# ===========================================================================
# save_report
# ===========================================================================

class TestSaveReport:

    def test_writes_valid_json(self, tmp_path: Path):
        gen = _mock_gen({c.prompt: "garbage" for c in DEFAULT_CASES[:2]})
        report = run_harness(generate_fn=gen, model="m", cases=DEFAULT_CASES[:2])
        path = save_report(report, base_dir=tmp_path, now=FROZEN_NOW)

        assert path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["model"] == "m"
        assert data["timestamp"] == FROZEN_NOW.isoformat()
        assert data["report_schema_version"] == "1"
        assert data["total_cases"] == 2

    def test_creates_missing_directory(self, tmp_path: Path):
        target = tmp_path / "deep" / "nested" / "dir"
        gen = _mock_gen({c.prompt: "garbage" for c in DEFAULT_CASES[:1]})
        report = run_harness(generate_fn=gen, model="m", cases=DEFAULT_CASES[:1])
        path = save_report(report, base_dir=target, now=FROZEN_NOW)
        assert target.is_dir()
        assert path.exists()

    def test_filename_includes_model_slug(self, tmp_path: Path):
        gen = _mock_gen({c.prompt: "garbage" for c in DEFAULT_CASES[:1]})
        report = run_harness(
            generate_fn=gen,
            model="qwen2.5-coder:7b",
            cases=DEFAULT_CASES[:1],
        )
        path = save_report(report, base_dir=tmp_path, now=FROZEN_NOW)
        assert path.name == "2026-05-27T143205Z_qwen2.5-coder-7b.json"

    def test_payload_round_trips(self, tmp_path: Path):
        gen = _mock_gen({c.prompt: "garbage" for c in DEFAULT_CASES[:3]})
        report = run_harness(generate_fn=gen, model="m", cases=DEFAULT_CASES[:3])
        path = save_report(report, base_dir=tmp_path, now=FROZEN_NOW)
        text = path.read_text(encoding="utf-8")
        # Pas de NaN/Inf : doit round-tripper en JSON strict.
        data = json.loads(text)
        assert data["case_scores"][0]["case_id"] == DEFAULT_CASES[0].id


# ===========================================================================
# run_and_save — orchestration
# ===========================================================================

class TestRunAndSave:

    def test_run_with_mock_perfect_score(self, tmp_path: Path):
        responses = {c.prompt: json.dumps(_build_ideal_ir(c)) for c in DEFAULT_CASES}
        report, path = run_and_save(
            model="mock-perfect",
            generate_fn=_mock_gen(responses),
            cases=DEFAULT_CASES,
            base_dir=tmp_path,
            now=FROZEN_NOW,
        )
        assert path.exists()
        assert report.model == "mock-perfect"
        assert report.parse_ok_rate == 1.0
        assert report.mean_score == pytest.approx(1.0)

        # Le rapport sauvegardé reflète le report en mémoire.
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["mean_score"] == pytest.approx(1.0)
        assert data["parse_ok_rate"] == 1.0

    def test_run_uses_default_model_when_none(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        monkeypatch.setenv("AAC_BLENDER_LLM_MODEL", "test-model-from-env")
        gen = _mock_gen({c.prompt: "" for c in DEFAULT_CASES[:1]})
        report, path = run_and_save(
            model=None,
            generate_fn=gen,
            cases=DEFAULT_CASES[:1],
            base_dir=tmp_path,
            now=FROZEN_NOW,
        )
        assert report.model == "test-model-from-env"
        assert "test-model-from-env" in path.name

    def test_run_returns_report_and_path(self, tmp_path: Path):
        gen = _mock_gen({c.prompt: "" for c in DEFAULT_CASES[:1]})
        report, path = run_and_save(
            model="m",
            generate_fn=gen,
            cases=DEFAULT_CASES[:1],
            base_dir=tmp_path,
            now=FROZEN_NOW,
        )
        assert isinstance(report, HarnessReport)
        assert isinstance(path, Path)


# ===========================================================================
# format_summary
# ===========================================================================

class TestFormatSummary:

    def test_summary_contains_key_fields(self, tmp_path: Path):
        gen = _mock_gen({c.prompt: "" for c in DEFAULT_CASES[:2]})
        report, path = run_and_save(
            model="m",
            generate_fn=gen,
            cases=DEFAULT_CASES[:2],
            base_dir=tmp_path,
            now=FROZEN_NOW,
        )
        text = format_summary(report, path)
        assert "model" in text
        assert "m" in text
        assert "total_cases" in text
        assert "parse_ok_rate" in text
        assert "mean_score" in text
        assert "per_field_accuracy" in text
        # Tous les case_id apparaissent.
        for s in report.case_scores:
            assert s.case_id in text
