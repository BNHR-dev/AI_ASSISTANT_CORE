"""
H.6.2 — Tests de l'eval harness Product Render IR.

Scope :
- Intégrité du corpus (ids uniques, clés expected dans le set autorisé,
  couleurs validées).
- Scoring déterministe (perfect / partial / parse-fail / color casse).
- Agrégation (mean_score, parse_ok_rate, per_field_accuracy).
- Orchestration via `run_harness` avec generate_fn mocké (zéro Ollama).

Aucune dépendance Ollama / réseau / disque.
"""
from __future__ import annotations

import json
from typing import Any, Callable

import pytest

from app.engine.product_render_eval_cases import (
    ALLOWED_EXPECTED_KEYS,
    DEFAULT_CASES,
    EvalCase,
)
from app.engine.product_render_eval_harness import (
    _flatten_intent,
    _values_match,
    report_to_dict,
    run_harness,
    score_case,
)
from app.engine.product_render_extractor import (
    ProductRenderExtractionResult,
)
from app.engine.product_render_ir import (
    BackdropIR,
    ProductRenderIntent,
    ProductSubjectIR,
)


# ===========================================================================
# Helpers
# ===========================================================================

def _intent_v0(
    *, kind="bottle", color="amber", material="glass", backdrop="neutral_gray"
) -> ProductRenderIntent:
    return ProductRenderIntent(
        schema_version="v0",
        subject=ProductSubjectIR(kind=kind, color=color, material=material),
        backdrop=BackdropIR(color=backdrop),
    )


def _intent_v1_full() -> ProductRenderIntent:
    return ProductRenderIntent(
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


def _result_parsed(intent: ProductRenderIntent) -> ProductRenderExtractionResult:
    return ProductRenderExtractionResult(
        intent=intent,
        status="parsed",
        raw_response="{}",
        extracted_json={},
        error=None,
        model="test-model",
    )


def _result_fallback(intent: ProductRenderIntent) -> ProductRenderExtractionResult:
    return ProductRenderExtractionResult(
        intent=intent,
        status="fallback",
        raw_response=None,
        extracted_json=None,
        error="empty_response",
        model="test-model",
    )


def _intent_to_json_response(intent: ProductRenderIntent) -> str:
    """Sérialise un intent en JSON tel que renvoyé par un LLM idéal."""
    return json.dumps(intent.model_dump(exclude_none=True))


def _mock_gen(responses_by_prompt: dict[str, str]) -> Callable[[str, str], str]:
    """
    Renvoie une `generate_fn` qui sélectionne la réponse selon le prompt.
    Match par sous-chaîne (le prompt complet contient le prompt utilisateur).
    """
    def _fn(model: str, prompt: str) -> str:
        for needle, resp in responses_by_prompt.items():
            if needle in prompt:
                return resp
        raise AssertionError(f"no mock response matched prompt: {prompt[:80]}")
    return _fn


# ===========================================================================
# Intégrité du corpus
# ===========================================================================

class TestCorpusIntegrity:

    def test_corpus_non_empty(self):
        assert len(DEFAULT_CASES) >= 10

    def test_corpus_ids_unique(self):
        ids = [c.id for c in DEFAULT_CASES]
        assert len(ids) == len(set(ids)), f"doublon : {ids}"

    def test_corpus_ids_kebab_case(self):
        for c in DEFAULT_CASES:
            assert c.id == c.id.lower()
            assert " " not in c.id
            assert "_" not in c.id

    def test_corpus_prompts_non_empty(self):
        for c in DEFAULT_CASES:
            assert c.prompt.strip(), f"prompt vide pour {c.id}"

    def test_corpus_expected_keys_all_allowed(self):
        for c in DEFAULT_CASES:
            unknown = set(c.expected) - ALLOWED_EXPECTED_KEYS
            assert not unknown, f"{c.id}: clés inconnues {unknown}"

    def test_corpus_contains_v0_and_v1(self):
        versions = {c.expected.get("schema_version") for c in DEFAULT_CASES}
        assert "v0" in versions
        assert "v1" in versions

    def test_corpus_v0_cases_have_no_v1_keys(self):
        v1_only = {
            "subject.shape", "subject.cap",
            "subject.transparency", "framing",
        }
        for c in DEFAULT_CASES:
            if c.expected.get("schema_version") == "v0":
                forbidden = v1_only & c.expected.keys()
                assert not forbidden, f"{c.id}: V0 + clés V1 {forbidden}"


class TestEvalCaseValidation:

    def test_unknown_key_raises(self):
        with pytest.raises(ValueError, match="clés expected inconnues"):
            EvalCase(id="x", prompt="p", expected={"bogus": "x"})

    def test_invalid_color_raises(self):
        with pytest.raises(ValueError):
            EvalCase(
                id="x", prompt="p",
                expected={"subject.color": "neon-pink-extreme"},
            )

    def test_v0_with_v1_key_raises(self):
        with pytest.raises(ValueError, match="interdit d'attendre des champs V1"):
            EvalCase(
                id="x", prompt="p",
                expected={"schema_version": "v0", "framing": "medium"},
            )


# ===========================================================================
# _flatten_intent
# ===========================================================================

class TestFlattenIntent:

    def test_flatten_v0(self):
        flat = _flatten_intent(_result_parsed(_intent_v0()))
        assert flat == {
            "schema_version": "v0",
            "subject.kind": "bottle",
            "subject.color": "amber",
            "subject.material": "glass",
            "backdrop.color": "neutral_gray",
        }

    def test_flatten_v1_includes_optional_when_set(self):
        flat = _flatten_intent(_result_parsed(_intent_v1_full()))
        assert flat["subject.shape"] == "rectangular"
        assert flat["subject.cap"] == "present"
        assert flat["subject.transparency"] == "glass"
        assert flat["framing"] == "close_packshot"

    def test_flatten_v1_omits_none(self):
        intent = ProductRenderIntent(
            schema_version="v1",
            subject=ProductSubjectIR(kind="jar", color="white", material="matte"),
            backdrop=BackdropIR(color="beige"),
        )
        flat = _flatten_intent(_result_parsed(intent))
        assert "subject.shape" not in flat
        assert "framing" not in flat


# ===========================================================================
# _values_match (couleurs + enums)
# ===========================================================================

class TestValuesMatch:

    def test_enum_exact_match(self):
        assert _values_match("subject.kind", "bottle", "bottle") is True
        assert _values_match("subject.kind", "bottle", "jar") is False

    def test_actual_none_is_mismatch(self):
        assert _values_match("subject.kind", "bottle", None) is False

    def test_color_case_insensitive(self):
        assert _values_match("subject.color", "AMBER", "amber") is True
        assert _values_match("backdrop.color", "Neutral_Gray", "neutral_gray") is True

    def test_color_hex_normalized(self):
        assert _values_match("subject.color", "#FFAA00", "#ffaa00") is True

    def test_color_mismatch(self):
        assert _values_match("subject.color", "red", "blue") is False

    def test_invalid_color_returns_false(self):
        # Un token invalide ne lève pas, retourne False (résistance au bruit).
        assert _values_match("subject.color", "amber", "amber-tinted") is False


# ===========================================================================
# score_case
# ===========================================================================

class TestScoreCase:

    def test_perfect_score(self):
        case = DEFAULT_CASES[0]  # v0 bottle amber glass neutral_gray
        result = _result_parsed(_intent_v0())
        sc = score_case(case, result)
        assert sc.parse_ok is True
        assert sc.score == 1.0
        assert all(sc.field_matches.values())
        assert sc.error is None

    def test_partial_score_one_mismatch(self):
        case = DEFAULT_CASES[0]  # 5 champs attendus
        wrong = _intent_v0(color="red")  # 1 mismatch sur 5
        sc = score_case(case, _result_parsed(wrong))
        assert sc.parse_ok is True
        assert sc.score == pytest.approx(4 / 5)
        assert sc.field_matches["subject.color"] is False
        assert sc.field_matches["subject.kind"] is True

    def test_parse_fail_forces_zero(self):
        case = DEFAULT_CASES[0]
        # Même si FALLBACK_INTENT correspond accidentellement, score=0.
        fb = _result_fallback(_intent_v0())  # même contenu que le canon
        sc = score_case(case, fb)
        assert sc.parse_ok is False
        assert sc.score == 0.0
        assert all(v is False for v in sc.field_matches.values())
        assert sc.error == "empty_response"
        assert sc.actual == {}

    def test_score_ignores_unexpected_fields(self):
        # Cas v0-sphere : ne contraint pas subject.color → ne pénalise pas.
        case = next(c for c in DEFAULT_CASES if c.id == "v0-sphere-metallic-cool-gray")
        intent = ProductRenderIntent(
            schema_version="v0",
            subject=ProductSubjectIR(
                kind="sphere",
                color="neutral_gray",  # n'importe quoi : pas dans expected
                material="metallic",
            ),
            backdrop=BackdropIR(color="cool_gray"),
        )
        sc = score_case(case, _result_parsed(intent))
        assert sc.score == 1.0
        assert "subject.color" not in sc.field_matches

    def test_v1_case_with_v1_intent_perfect(self):
        case = next(
            c for c in DEFAULT_CASES
            if c.id == "v1-bottle-rectangular-amber-glass-cap-closeup"
        )
        sc = score_case(case, _result_parsed(_intent_v1_full()))
        assert sc.score == 1.0
        assert sc.field_matches["subject.shape"] is True
        assert sc.field_matches["framing"] is True

    def test_v1_field_missing_is_mismatch(self):
        case = next(
            c for c in DEFAULT_CASES
            if c.id == "v1-bottle-rectangular-amber-glass-cap-closeup"
        )
        # IR v1 minimale : pas de shape/cap/transparency/framing.
        intent = ProductRenderIntent(
            schema_version="v1",
            subject=ProductSubjectIR(kind="bottle", color="amber", material="glass"),
            backdrop=BackdropIR(color="neutral_gray"),
        )
        sc = score_case(case, _result_parsed(intent))
        # 5 champs satisfaits (schema_version, kind, color, material, backdrop.color)
        # sur 9 attendus → score = 5/9.
        assert sc.score == pytest.approx(5 / 9)
        assert sc.field_matches["framing"] is False
        assert sc.field_matches["subject.shape"] is False

    def test_color_match_tolerates_case(self):
        case = EvalCase(
            id="t", prompt="p",
            expected={
                "schema_version": "v0",
                "subject.kind": "bottle",
                "subject.color": "AMBER",  # casse différente
                "subject.material": "glass",
                "backdrop.color": "neutral_gray",
            },
        )
        sc = score_case(case, _result_parsed(_intent_v0()))
        assert sc.score == 1.0
        assert sc.field_matches["subject.color"] is True

    def test_empty_expected_yields_zero(self):
        # Cas dégénéré : pas d'attente. Le harness ne crashe pas.
        case = EvalCase(id="t", prompt="p", expected={})
        sc = score_case(case, _result_parsed(_intent_v0()))
        assert sc.score == 0.0
        assert sc.field_matches == {}


# ===========================================================================
# run_harness — orchestration avec mock
# ===========================================================================

class TestRunHarness:

    def test_run_with_perfect_mock_returns_full_score(self):
        # Pour chaque cas, on construit une réponse JSON parfaitement alignée
        # avec son `expected` (en complétant V1 si nécessaire).
        responses: dict[str, str] = {}
        for case in DEFAULT_CASES:
            ir_dict = _build_ideal_ir(case)
            responses[case.prompt] = json.dumps(ir_dict)

        report = run_harness(
            generate_fn=_mock_gen(responses),
            model="mock-perfect",
            cases=DEFAULT_CASES,
        )
        assert report.total_cases == len(DEFAULT_CASES)
        assert report.parse_ok_rate == 1.0
        assert report.mean_score == pytest.approx(1.0)
        assert all(s.score == pytest.approx(1.0) for s in report.case_scores)
        assert all(v == pytest.approx(1.0) for v in report.per_field_accuracy.values())
        assert report.model == "mock-perfect"

    def test_run_with_all_garbage_returns_zero(self):
        gen = _mock_gen({c.prompt: "not json at all" for c in DEFAULT_CASES})
        report = run_harness(generate_fn=gen, model="mock-bad", cases=DEFAULT_CASES)
        assert report.parse_ok_rate == 0.0
        assert report.mean_score == 0.0
        assert all(s.parse_ok is False for s in report.case_scores)
        # per_field_accuracy : toutes les clés présentes dans le corpus, toutes à 0.
        for key, acc in report.per_field_accuracy.items():
            assert acc == 0.0, key

    def test_run_with_mixed_results_aggregates_correctly(self):
        # Cas 0 → parfait, tous les autres → réponse vide (fallback).
        responses: dict[str, str] = {}
        for i, case in enumerate(DEFAULT_CASES):
            if i == 0:
                responses[case.prompt] = json.dumps(_build_ideal_ir(case))
            else:
                responses[case.prompt] = ""  # → fallback parse

        report = run_harness(
            generate_fn=_mock_gen(responses),
            model="mock-mixed",
            cases=DEFAULT_CASES,
        )
        assert report.parse_ok_rate == pytest.approx(1 / len(DEFAULT_CASES))
        # Seul le cas 0 contribue 1.0 ; tous les autres contribuent 0.0.
        expected_mean = 1.0 / len(DEFAULT_CASES)
        assert report.mean_score == pytest.approx(expected_mean)

    def test_run_uses_default_model_when_none(self, monkeypatch: pytest.MonkeyPatch):
        # On force AAC_BLENDER_LLM_MODEL pour vérifier la propagation.
        monkeypatch.setenv("AAC_BLENDER_LLM_MODEL", "test-default-model")
        gen = _mock_gen({c.prompt: "garbage" for c in DEFAULT_CASES})
        report = run_harness(generate_fn=gen, model=None, cases=DEFAULT_CASES[:2])
        assert report.model == "test-default-model"

    def test_run_with_subset_of_cases(self):
        subset = DEFAULT_CASES[:3]
        gen = _mock_gen({c.prompt: "" for c in subset})
        report = run_harness(generate_fn=gen, model="m", cases=subset)
        assert report.total_cases == 3
        assert len(report.case_scores) == 3


# ===========================================================================
# report_to_dict — sérialisation
# ===========================================================================

class TestReportSerialization:

    def test_report_to_dict_is_json_serializable(self):
        gen = _mock_gen({c.prompt: "garbage" for c in DEFAULT_CASES[:2]})
        report = run_harness(generate_fn=gen, model="m", cases=DEFAULT_CASES[:2])
        d = report_to_dict(report)
        # Doit pouvoir round-tripper en JSON.
        s = json.dumps(d, ensure_ascii=False)
        back = json.loads(s)
        assert back["model"] == "m"
        assert back["total_cases"] == 2
        assert len(back["case_scores"]) == 2


# ===========================================================================
# Helper : construire l'IR idéale pour un EvalCase
# ===========================================================================

def _build_ideal_ir(case: EvalCase) -> dict[str, Any]:
    """
    Construit un dict IR qui SATISFAIT TOUTES les attentes du cas et reste
    valide selon Pydantic. Utilisé pour le test "perfect mock".

    Stratégie : on part des attentes, on complète les champs obligatoires
    manquants par des valeurs neutres valides.
    """
    sv = case.expected.get("schema_version", "v0")
    ir: dict[str, Any] = {"schema_version": sv}
    subject: dict[str, Any] = {
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
    ir["backdrop"] = {
        "color": case.expected.get("backdrop.color", "neutral_gray"),
    }
    if sv == "v1" and "framing" in case.expected:
        ir["framing"] = case.expected["framing"]
    if sv == "v1" and "pedestal.color" in case.expected:
        ir["pedestal"] = {"color": case.expected["pedestal.color"]}
        if "pedestal.material" in case.expected:
            ir["pedestal"]["material"] = case.expected["pedestal.material"]
    return ir
