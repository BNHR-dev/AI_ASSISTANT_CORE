"""
H.6.8.a — Tests du corpus `script_gen_eval_cases`.

Vérifie l'intégrité statique du corpus :
- 5 cas exactement (cadrage validé).
- Répartition 2 freeform + 2 interior_space + 1 ambiguous.
- IDs uniques.
- Tous les checks listés appartiennent à ALL_CHECKS.
- Cohérence template ↔ must_name_objects ↔ applicable_checks.
- Pas de cas product_render (interdit en H.6.8.a).

Tests purs : aucune dépendance LLM, Blender, I/O.
"""
from __future__ import annotations

import pytest

from app.engine.blender_templates import TEMPLATE_SPECS
from app.engine.script_gen_eval_cases import (
    ALL_CHECKS,
    ALLOWED_EXPECTED_KEYS,
    CHECK_TEMPLATE_FORBIDDEN_PREFIX,
    CHECK_TEMPLATE_REQUIRED_OBJECTS,
    DEFAULT_CASES,
    ScriptGenCase,
    _validate_corpus,
)


# ---------------------------------------------------------------------------
# Corpus structure
# ---------------------------------------------------------------------------

def test_default_cases_count_is_five() -> None:
    assert len(DEFAULT_CASES) == 5


def test_default_cases_have_unique_ids() -> None:
    ids = [c.id for c in DEFAULT_CASES]
    assert len(ids) == len(set(ids))


def test_default_cases_category_distribution() -> None:
    cats = [c.category for c in DEFAULT_CASES]
    assert cats.count("freeform") == 2
    assert cats.count("interior_space") == 2
    assert cats.count("ambiguous") == 1


def test_default_cases_ids_match_expected() -> None:
    expected = {
        "freeform_metal_sphere_floating",
        "freeform_low_poly_tree",
        "interior_salon_moderne",
        "interior_cuisine_industrielle",
        "ambiguous_atelier_artiste",
    }
    assert {c.id for c in DEFAULT_CASES} == expected


# ---------------------------------------------------------------------------
# Expected validation
# ---------------------------------------------------------------------------

def test_each_case_has_only_allowed_expected_keys() -> None:
    for case in DEFAULT_CASES:
        unknown = set(case.expected.keys()) - ALLOWED_EXPECTED_KEYS
        assert not unknown, f"Case {case.id} a des clés non autorisées: {unknown}"


def test_each_case_applicable_checks_are_known() -> None:
    for case in DEFAULT_CASES:
        applicable = case.expected.get("applicable_checks", [])
        unknown = set(applicable) - ALL_CHECKS
        assert not unknown, f"Case {case.id} a des checks inconnus: {unknown}"


def test_each_case_applicable_checks_non_empty() -> None:
    for case in DEFAULT_CASES:
        assert case.expected.get("applicable_checks"), (
            f"Case {case.id} a applicable_checks vide"
        )


def test_freeform_cases_have_no_template_required_check() -> None:
    for case in DEFAULT_CASES:
        if case.expected.get("template") is None:
            applicable = case.expected.get("applicable_checks", [])
            assert CHECK_TEMPLATE_REQUIRED_OBJECTS not in applicable, (
                f"Case {case.id} (template=None) ne doit pas avoir "
                f"{CHECK_TEMPLATE_REQUIRED_OBJECTS} dans applicable_checks"
            )


def test_interior_cases_have_template_required_check() -> None:
    for case in DEFAULT_CASES:
        if case.expected.get("template") == "interior_space":
            applicable = case.expected.get("applicable_checks", [])
            assert CHECK_TEMPLATE_REQUIRED_OBJECTS in applicable, (
                f"Case {case.id} (interior_space) doit lister "
                f"{CHECK_TEMPLATE_REQUIRED_OBJECTS}"
            )


def test_freeform_cases_have_empty_must_name_objects() -> None:
    for case in DEFAULT_CASES:
        if case.expected.get("template") is None:
            assert case.expected.get("must_name_objects") == [], (
                f"Case {case.id} (template=None) doit avoir "
                f"must_name_objects vide"
            )


def test_interior_cases_must_name_objects_subset_of_spec() -> None:
    spec_required = set(TEMPLATE_SPECS["interior_space"]["required_objects"])
    for case in DEFAULT_CASES:
        if case.expected.get("template") == "interior_space":
            names = set(case.expected.get("must_name_objects", []))
            assert names, f"Case {case.id} (interior) doit lister des objets"
            assert names.issubset(spec_required), (
                f"Case {case.id} liste des objets hors TEMPLATE_SPECS: "
                f"{names - spec_required}"
            )


def test_no_product_render_case_in_h68a_corpus() -> None:
    """H.6.8.a interdit explicitement les cas product_render fallback."""
    for case in DEFAULT_CASES:
        assert case.expected.get("template") != "product_render", (
            f"Case {case.id} a template=product_render : interdit en H.6.8.a"
        )


# ---------------------------------------------------------------------------
# ScriptGenCase __post_init__ validation
# ---------------------------------------------------------------------------

def test_scriptgencase_rejects_empty_id() -> None:
    with pytest.raises(ValueError, match="id invalide"):
        ScriptGenCase(
            id="",
            prompt="x",
            category="freeform",
            expected={"applicable_checks": ["generation_ok"]},
        )


def test_scriptgencase_rejects_empty_prompt() -> None:
    with pytest.raises(ValueError, match="prompt invalide"):
        ScriptGenCase(
            id="x",
            prompt="",
            category="freeform",
            expected={"applicable_checks": ["generation_ok"]},
        )


def test_scriptgencase_rejects_unknown_category() -> None:
    with pytest.raises(ValueError, match="category invalide"):
        ScriptGenCase(
            id="x",
            prompt="x",
            category="weird",
            expected={"applicable_checks": ["generation_ok"]},
        )


def test_scriptgencase_rejects_unknown_expected_key() -> None:
    with pytest.raises(ValueError, match="clés expected non autorisées"):
        ScriptGenCase(
            id="x",
            prompt="x",
            category="freeform",
            expected={
                "applicable_checks": ["generation_ok"],
                "unknown_key": "x",
            },
        )


def test_scriptgencase_rejects_product_render_template() -> None:
    with pytest.raises(ValueError, match="template invalide en H.6.8.a"):
        ScriptGenCase(
            id="x",
            prompt="x",
            category="freeform",
            expected={
                "template": "product_render",
                "applicable_checks": ["generation_ok"],
            },
        )


def test_scriptgencase_rejects_must_name_objects_when_no_template() -> None:
    with pytest.raises(ValueError, match="must_name_objects doit être vide"):
        ScriptGenCase(
            id="x",
            prompt="x",
            category="freeform",
            expected={
                "template": None,
                "must_name_objects": ["Camera"],
                "applicable_checks": ["generation_ok"],
            },
        )


def test_scriptgencase_rejects_must_name_objects_outside_spec() -> None:
    with pytest.raises(ValueError, match="hors TEMPLATE_SPECS"):
        ScriptGenCase(
            id="x",
            prompt="x",
            category="interior_space",
            expected={
                "template": "interior_space",
                "must_name_objects": ["Made_Up_Object_Name"],
                "applicable_checks": ["generation_ok"],
            },
        )


def test_scriptgencase_rejects_template_required_check_when_no_template() -> None:
    with pytest.raises(ValueError, match=CHECK_TEMPLATE_REQUIRED_OBJECTS):
        ScriptGenCase(
            id="x",
            prompt="x",
            category="freeform",
            expected={
                "template": None,
                "applicable_checks": [
                    "generation_ok",
                    CHECK_TEMPLATE_REQUIRED_OBJECTS,
                ],
            },
        )


def test_scriptgencase_rejects_empty_applicable_checks() -> None:
    with pytest.raises(ValueError, match="applicable_checks ne peut pas être vide"):
        ScriptGenCase(
            id="x",
            prompt="x",
            category="freeform",
            expected={"applicable_checks": []},
        )


def test_scriptgencase_rejects_unknown_check() -> None:
    with pytest.raises(ValueError, match="checks inconnus"):
        ScriptGenCase(
            id="x",
            prompt="x",
            category="freeform",
            expected={"applicable_checks": ["totally_made_up_check"]},
        )


# ---------------------------------------------------------------------------
# Corpus-level validator
# ---------------------------------------------------------------------------

def test_validate_corpus_detects_duplicate_ids() -> None:
    c = ScriptGenCase(
        id="dup",
        prompt="x",
        category="freeform",
        expected={"applicable_checks": ["generation_ok"]},
    )
    with pytest.raises(ValueError, match="ids dupliqués"):
        _validate_corpus((c, c))


# ---------------------------------------------------------------------------
# Smoke : ALL_CHECKS exhaustivité
# ---------------------------------------------------------------------------

def test_check_template_forbidden_prefix_is_in_all_checks() -> None:
    """Sanity : le check existe même s'il n'est pas applicable dans H.6.8.a."""
    assert CHECK_TEMPLATE_FORBIDDEN_PREFIX in ALL_CHECKS
