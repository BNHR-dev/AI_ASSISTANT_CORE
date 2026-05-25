"""
Tests unitaires — blender_runtime_contract.evaluate_runtime_contract (H.4.8).

Fonctions PURES — aucun subprocess Blender requis.
"""
from __future__ import annotations

from app.engine.blender_runtime_contract import (
    RUNTIME_CONTRACT_SPECS,
    V_TEMPLATE_FORBIDDEN_OBJECT_PREFIX,
    V_TEMPLATE_REQUIRED_MISSING_PREFIX,
    evaluate_runtime_contract,
    get_runtime_contract_spec,
)


# ---------------------------------------------------------------------------
# Specs structure
# ---------------------------------------------------------------------------

def test_runtime_contract_specs_contains_product_render():
    spec = RUNTIME_CONTRACT_SPECS["product_render"]
    assert "required_objects" in spec
    assert "forbidden_objects" in spec
    assert "Key_Light" in spec["required_objects"]
    assert "Fill_Light" in spec["required_objects"]
    assert "Product_Subject" in spec["required_objects"]
    assert "Sun" in spec["forbidden_objects"]


def test_get_runtime_contract_spec_unknown_returns_none():
    assert get_runtime_contract_spec(None) is None
    assert get_runtime_contract_spec("") is None
    assert get_runtime_contract_spec("not_a_template") is None


def test_get_runtime_contract_spec_product_render():
    spec = get_runtime_contract_spec("product_render")
    assert spec is not None
    assert "Camera" in spec["required_objects"]


# ---------------------------------------------------------------------------
# evaluate_runtime_contract — cas produit_render
# ---------------------------------------------------------------------------

def test_passed_when_full_contract_satisfied():
    object_names = [
        "Backdrop_Plane", "Pedestal", "Product_Subject",
        "Camera", "Key_Light", "Fill_Light",
    ]
    result = evaluate_runtime_contract(object_names, "product_render")
    assert result["status"] == "passed"
    assert result["violations"] == []
    assert result["required_missing"] == []
    assert result["forbidden_present"] == []
    assert set(result["required_present"]) == set(object_names)


def test_detects_key_light_missing():
    object_names = [
        "Backdrop_Plane", "Pedestal", "Product_Subject",
        "Camera", "Fill_Light",
    ]
    result = evaluate_runtime_contract(object_names, "product_render")
    assert result["status"] == "degraded"
    assert f"{V_TEMPLATE_REQUIRED_MISSING_PREFIX}Key_Light" in result["violations"]
    assert "Key_Light" in result["required_missing"]
    assert "Fill_Light" not in result["required_missing"]


def test_detects_fill_light_missing():
    object_names = [
        "Backdrop_Plane", "Pedestal", "Product_Subject",
        "Camera", "Key_Light",
    ]
    result = evaluate_runtime_contract(object_names, "product_render")
    assert result["status"] == "degraded"
    assert f"{V_TEMPLATE_REQUIRED_MISSING_PREFIX}Fill_Light" in result["violations"]
    assert "Fill_Light" in result["required_missing"]


def test_detects_sun_as_forbidden():
    object_names = [
        "Backdrop_Plane", "Pedestal", "Product_Subject",
        "Camera", "Key_Light", "Fill_Light", "Sun",
    ]
    result = evaluate_runtime_contract(object_names, "product_render")
    assert result["status"] == "degraded"
    assert f"{V_TEMPLATE_FORBIDDEN_OBJECT_PREFIX}Sun" in result["violations"]
    assert "Sun" in result["forbidden_present"]


def test_detects_smoke_h47_state():
    """État réel du smoke H.4.7 f5f6c34c : Key_Light + Fill_Light manquants, Sun présent."""
    object_names = [
        "Backdrop_Plane", "Pedestal", "Product_Subject",
        "Camera", "Sun",
    ]
    result = evaluate_runtime_contract(object_names, "product_render")
    assert result["status"] == "degraded"
    assert f"{V_TEMPLATE_REQUIRED_MISSING_PREFIX}Key_Light" in result["violations"]
    assert f"{V_TEMPLATE_REQUIRED_MISSING_PREFIX}Fill_Light" in result["violations"]
    assert f"{V_TEMPLATE_FORBIDDEN_OBJECT_PREFIX}Sun" in result["violations"]


def test_required_missing_and_forbidden_combined():
    object_names = ["Product_Subject", "Sun"]
    result = evaluate_runtime_contract(object_names, "product_render")
    assert result["status"] == "degraded"
    # 5 required absents (Backdrop_Plane, Pedestal, Camera, Key_Light, Fill_Light)
    # + 1 forbidden present (Sun)
    assert len(result["violations"]) == 6


# ---------------------------------------------------------------------------
# evaluate_runtime_contract — cas edge
# ---------------------------------------------------------------------------

def test_template_none_returns_skipped():
    result = evaluate_runtime_contract(["Anything"], None)
    assert result["status"] == "skipped"
    assert result["violations"] == []
    assert result["template_name"] is None


def test_template_unknown_returns_skipped():
    result = evaluate_runtime_contract(["Anything"], "not_a_template")
    assert result["status"] == "skipped"
    assert result["violations"] == []


def test_object_names_none_returns_skipped():
    result = evaluate_runtime_contract(None, "product_render")
    assert result["status"] == "skipped"
    assert result["violations"] == []


def test_empty_object_names_returns_all_required_missing():
    result = evaluate_runtime_contract([], "product_render")
    assert result["status"] == "degraded"
    # Tous les required absents → 6 violations required_missing, aucune forbidden
    required = RUNTIME_CONTRACT_SPECS["product_render"]["required_objects"]
    assert len(result["required_missing"]) == len(required)
    assert result["forbidden_present"] == []


def test_interior_space_not_yet_in_specs_returns_skipped():
    """interior_space n'est pas (encore) dans RUNTIME_CONTRACT_SPECS en V0."""
    result = evaluate_runtime_contract(
        ["Floor_Plane", "Wall_Back", "Main_Subject", "Camera", "Key_Light"],
        "interior_space",
    )
    assert result["status"] == "skipped"
    assert result["violations"] == []


def test_violation_string_format():
    """Vérifie le format exact des préfixes pour stabilité d'intégration."""
    result = evaluate_runtime_contract(["Product_Subject", "Sun"], "product_render")
    for v in result["violations"]:
        assert v.startswith(V_TEMPLATE_REQUIRED_MISSING_PREFIX) or v.startswith(V_TEMPLATE_FORBIDDEN_OBJECT_PREFIX)


def test_result_always_has_expected_keys():
    result = evaluate_runtime_contract(["X"], "product_render")
    for key in (
        "status", "template_name", "violations",
        "required_present", "required_missing", "forbidden_present",
    ):
        assert key in result
