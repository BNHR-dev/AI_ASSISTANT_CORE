"""
semantic_fidelity_v1 — Tests de la phase fidélité sémantique product_render.

Couvre :
- IR : kind "watch", subject.label / subject.kind_fidelity (version-neutres),
  PedestalIR (V1-only) ;
- extracteur : prompt enrichi + normalizers (label tronqué, kind_fidelity
  hors-enum, pedestal malformé, downgrade v1→v0 qui préserve les métadonnées) ;
- builder : géométrie watch (rotation, pose sur pedestal), socle paramétré V1,
  en-tête de traçabilité, non-régression des kinds historiques.

Aucun appel LLM : tout passe par parse_product_render_intent_from_text ou
un generate_fn injecté.
"""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app.engine.product_render_builder import (
    PEDESTAL_TOP_Z,
    SUBJECT_GEOMETRY,
    build_product_render_scene_script,
)
from app.engine.product_render_extractor import (
    build_extraction_prompt,
    extract_product_render_intent,
    parse_product_render_intent_from_text,
)
from app.engine.product_render_ir import (
    PedestalIR,
    ProductRenderIntent,
    SUBJECT_LABEL_MAX_LEN,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _v0_subject(**overrides) -> dict:
    subject = {"kind": "bottle", "color": "amber", "material": "glass"}
    subject.update(overrides)
    return subject


def _intent_dict(schema_version: str = "v0", **top_overrides) -> dict:
    data = {
        "schema_version": schema_version,
        "subject": _v0_subject(),
        "backdrop": {"color": "neutral_gray"},
    }
    data.update(top_overrides)
    return data


def _parse(data: dict):
    return parse_product_render_intent_from_text(json.dumps(data))


# ---------------------------------------------------------------------------
# IR — kind watch
# ---------------------------------------------------------------------------

def test_ir_accepts_watch_kind():
    intent = ProductRenderIntent(**_intent_dict(subject=_v0_subject(kind="watch")))
    assert intent.subject.kind == "watch"


def test_watch_present_in_subject_geometry():
    assert "watch" in SUBJECT_GEOMETRY
    geom = SUBJECT_GEOMETRY["watch"]
    # Le disque est tourné face caméra → l'étendue verticale est le rayon.
    assert geom["half_h"] == geom["radius"]
    assert "rotation_euler" in geom


# ---------------------------------------------------------------------------
# IR — label / kind_fidelity (version-neutres)
# ---------------------------------------------------------------------------

def test_label_and_fidelity_allowed_in_v0():
    intent = ProductRenderIntent(
        **_intent_dict(
            subject=_v0_subject(label="chronomètre métal poli", kind_fidelity="approximate")
        )
    )
    assert intent.schema_version == "v0"
    assert intent.subject.label == "chronomètre métal poli"
    assert intent.subject.kind_fidelity == "approximate"


def test_label_stripped_and_empty_becomes_none():
    intent = ProductRenderIntent(
        **_intent_dict(subject=_v0_subject(label="   "))
    )
    assert intent.subject.label is None
    intent2 = ProductRenderIntent(
        **_intent_dict(subject=_v0_subject(label="  flacon  "))
    )
    assert intent2.subject.label == "flacon"


def test_label_too_long_rejected_at_ir_level():
    with pytest.raises(ValidationError):
        ProductRenderIntent(
            **_intent_dict(subject=_v0_subject(label="x" * (SUBJECT_LABEL_MAX_LEN + 1)))
        )


def test_kind_fidelity_out_of_enum_rejected():
    with pytest.raises(ValidationError):
        ProductRenderIntent(
            **_intent_dict(subject=_v0_subject(kind_fidelity="exactish"))
        )


# ---------------------------------------------------------------------------
# IR — pedestal (V1-only)
# ---------------------------------------------------------------------------

def test_pedestal_forbidden_in_v0():
    with pytest.raises(ValidationError):
        ProductRenderIntent(
            **_intent_dict(pedestal={"color": "warm_gray"})
        )


def test_pedestal_allowed_in_v1_with_default_material():
    intent = ProductRenderIntent(
        **_intent_dict("v1", pedestal={"color": "warm_gray"})
    )
    assert intent.pedestal == PedestalIR(color="warm_gray", material="matte")


# ---------------------------------------------------------------------------
# Normalizers (via parse_product_render_intent_from_text, end-to-end)
# ---------------------------------------------------------------------------

def test_normalizer_truncates_long_label():
    long_label = "chronomètre " * 30  # >> 120 chars
    result = _parse(_intent_dict(subject=_v0_subject(label=long_label)))
    assert result.status == "parsed"
    assert len(result.intent.subject.label) <= SUBJECT_LABEL_MAX_LEN


def test_normalizer_drops_non_string_label():
    result = _parse(_intent_dict(subject=_v0_subject(label={"oops": 1})))
    assert result.status == "parsed"
    assert result.intent.subject.label is None


def test_normalizer_drops_invalid_kind_fidelity():
    result = _parse(_intent_dict(subject=_v0_subject(kind_fidelity="mostly_exact")))
    assert result.status == "parsed"
    assert result.intent.subject.kind_fidelity is None


def test_normalizer_drops_malformed_pedestal_then_downgrades_to_v0():
    # pedestal string (malformé) était le seul signal V1 → après drop,
    # downgrade v0 ; le label (version-neutre) survit.
    data = _intent_dict(
        "v1",
        subject=_v0_subject(label="flacon ambré"),
        pedestal="stone",
    )
    result = _parse(data)
    assert result.status == "parsed"
    assert result.intent.schema_version == "v0"
    assert result.intent.pedestal is None
    assert result.intent.subject.label == "flacon ambré"


def test_normalizer_drops_pedestal_without_color():
    data = _intent_dict("v1", pedestal={"material": "matte"})
    result = _parse(data)
    assert result.status == "parsed"
    assert result.intent.pedestal is None


def test_normalizer_fixes_hallucinated_pedestal_material():
    data = _intent_dict("v1", pedestal={"color": "warm_gray", "material": "stone"})
    result = _parse(data)
    assert result.status == "parsed"
    assert result.intent.pedestal.material == "matte"
    assert result.intent.pedestal.color == "warm_gray"


def test_normalizer_pedestal_color_hex_and_safety_default():
    # Hex CSS commun → palette.
    data = _intent_dict("v1", pedestal={"color": "#808080"})
    result = _parse(data)
    assert result.intent.pedestal.color == "neutral_gray"
    # Couleur inventée → safety default.
    data2 = _intent_dict("v1", pedestal={"color": "granite"})
    result2 = _parse(data2)
    assert result2.intent.pedestal.color == "neutral_gray"


def test_pedestal_alone_keeps_v1():
    data = _intent_dict("v1", pedestal={"color": "warm_gray"})
    result = _parse(data)
    assert result.status == "parsed"
    assert result.intent.schema_version == "v1"
    assert result.intent.pedestal is not None


# ---------------------------------------------------------------------------
# Prompt d'extraction
# ---------------------------------------------------------------------------

def test_extraction_prompt_mentions_new_fields():
    prompt = build_extraction_prompt("chronomètre métal poli sur socle pierre")
    assert "watch" in prompt
    assert "label" in prompt
    assert "kind_fidelity" in prompt
    assert "pedestal" in prompt
    assert "chronomètre" in prompt  # hint lexical


def test_extraction_end_to_end_with_injected_llm():
    payload = {
        "schema_version": "v1",
        "subject": {
            "kind": "watch",
            "color": "neutral_gray",
            "material": "metallic",
            "label": "chronomètre métal poli",
            "kind_fidelity": "exact",
        },
        "backdrop": {"color": "neutral_gray"},
        "pedestal": {"color": "warm_gray", "material": "matte"},
        "framing": "close_packshot",
    }
    result = extract_product_render_intent(
        "packshot chronomètre métal poli sur socle pierre",
        generate_fn=lambda model, prompt: json.dumps(payload),
    )
    assert result.status == "parsed"
    intent = result.intent
    assert intent.subject.kind == "watch"
    assert intent.subject.label == "chronomètre métal poli"
    assert intent.subject.kind_fidelity == "exact"
    assert intent.pedestal.color == "warm_gray"


# ---------------------------------------------------------------------------
# Builder — watch
# ---------------------------------------------------------------------------

def test_v0_watch_script_has_rotation_and_sits_on_pedestal():
    intent = ProductRenderIntent(**_intent_dict(subject=_v0_subject(kind="watch")))
    script = build_product_render_scene_script(intent)
    geom = SUBJECT_GEOMETRY["watch"]
    assert f"product.rotation_euler = {geom['rotation_euler']}" in script
    expected_z = PEDESTAL_TOP_Z + geom["half_h"]
    assert f"location=(0.0, 0.0, {expected_z})" in script


def test_v0_non_watch_script_has_no_subject_rotation():
    intent = ProductRenderIntent(**_intent_dict())
    script = build_product_render_scene_script(intent)
    assert "product.rotation_euler" not in script


def test_v1_watch_script_has_rotation():
    intent = ProductRenderIntent(
        **_intent_dict("v1", subject=_v0_subject(kind="watch"), framing="close_packshot")
    )
    script = build_product_render_scene_script(intent)
    geom = SUBJECT_GEOMETRY["watch"]
    assert f"product.rotation_euler = {geom['rotation_euler']}" in script


def test_v1_watch_shape_does_not_override_disc_silhouette():
    """Observé au smoke 2026-06-12 : le LLM posait shape=rectangular sur un
    chronomètre → cube sans rotation. La silhouette watch est intrinsèque."""
    intent = ProductRenderIntent(
        **_intent_dict(
            "v1", subject=_v0_subject(kind="watch", shape="rectangular")
        )
    )
    script = build_product_render_scene_script(intent)
    geom = SUBJECT_GEOMETRY["watch"]
    assert f"product.rotation_euler = {geom['rotation_euler']}" in script
    assert "primitive_cube_add" not in script.split("Product_Subject")[1].split("Camera")[0]


# ---------------------------------------------------------------------------
# Builder — pedestal paramétré
# ---------------------------------------------------------------------------

def test_v1_pedestal_parametrized_in_script():
    intent = ProductRenderIntent(
        **_intent_dict("v1", pedestal={"color": "warm_gray", "material": "glossy"})
    )
    script = build_product_render_scene_script(intent)
    assert "# --- Pedestal (IR pedestal, semantic_fidelity_v1) ---" in script
    # warm_gray résolu en RGBA (cf. NAMED_COLOR_PALETTE).
    assert "(0.55, 0.5, 0.45, 1.0)" in script
    # Géométrie canonique préservée (PEDESTAL_TOP_Z inchangé).
    assert "radius=0.1, depth=0.04" in script


def test_v1_without_pedestal_keeps_canonical():
    intent = ProductRenderIntent(**_intent_dict("v1", framing="medium"))
    script = build_product_render_scene_script(intent)
    assert "# --- Pedestal (canonique) ---" in script
    assert "(0.3, 0.3, 0.3, 1.0)" in script


# ---------------------------------------------------------------------------
# Builder — en-tête de traçabilité
# ---------------------------------------------------------------------------

def test_script_header_contains_label_and_fidelity():
    intent = ProductRenderIntent(
        **_intent_dict(
            subject=_v0_subject(
                kind="watch",
                label="chronomètre métal poli",
                kind_fidelity="exact",
            )
        )
    )
    script = build_product_render_scene_script(intent)
    assert "# subject.label = 'chronomètre métal poli'" in script
    assert "# subject.kind_fidelity = 'exact'" in script


def test_script_header_silent_without_metadata():
    intent = ProductRenderIntent(**_intent_dict())
    script = build_product_render_scene_script(intent)
    assert "# subject.label" not in script
    assert "# subject.kind_fidelity" not in script


# ---------------------------------------------------------------------------
# Non-régression — l'occupation verticale des kinds reste bornée
# ---------------------------------------------------------------------------

def test_watch_height_within_historical_kind_range():
    """La hauteur effective de watch (diamètre) reste dans la plage des
    kinds validés H.6.9 (0.10–0.22 m) : pas de régression de cadrage."""
    heights = []
    for kind, geom in SUBJECT_GEOMETRY.items():
        if "half_h" in geom:
            heights.append((kind, geom["half_h"] * 2.0))
        elif "depth" in geom:
            heights.append((kind, geom["depth"]))
        elif "size" in geom:
            heights.append((kind, geom["size"]))
        else:
            heights.append((kind, geom["radius"] * 2.0))
    by_kind = dict(heights)
    assert 0.10 <= by_kind["watch"] <= 0.22
