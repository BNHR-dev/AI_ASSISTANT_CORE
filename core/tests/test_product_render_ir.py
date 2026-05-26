"""
H.5.1 — Tests unitaires Pydantic ProductRenderIntent V0.

Fonctions PURES — pas d'I/O, pas de Blender, pas de LLM.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.engine.product_render_ir import (
    NAMED_COLOR_PALETTE,
    BackdropIR,
    ProductRenderIntent,
    ProductSubjectIR,
    _validate_color_token,
    resolve_color,
)


# ---------------------------------------------------------------------------
# Schéma : cas nominal
# ---------------------------------------------------------------------------

def test_valid_ir_nominal_bottle_amber_glass():
    """Cas H.5.1 par défaut : bouteille ambrée en verre sur fond gris neutre."""
    ir = ProductRenderIntent(
        schema_version="v0",
        subject=ProductSubjectIR(kind="bottle", color="amber", material="glass"),
        backdrop=BackdropIR(color="neutral_gray"),
    )
    assert ir.schema_version == "v0"
    assert ir.subject.kind == "bottle"
    assert ir.subject.color == "amber"
    assert ir.subject.material == "glass"
    assert ir.backdrop.color == "neutral_gray"


def test_valid_ir_accepts_hex_color_for_subject_and_backdrop():
    """Les couleurs peuvent être des codes hex #RRGGBB."""
    ir = ProductRenderIntent(
        schema_version="v0",
        subject=ProductSubjectIR(kind="cylinder", color="#a83232", material="matte"),
        backdrop=BackdropIR(color="#f0f0f0"),
    )
    assert ir.subject.color == "#a83232"
    assert ir.backdrop.color == "#f0f0f0"


# ---------------------------------------------------------------------------
# Validation des enums
# ---------------------------------------------------------------------------

def test_invalid_subject_kind_rejected():
    with pytest.raises(ValidationError) as exc:
        ProductSubjectIR(kind="rocket", color="red", material="matte")
    msg = str(exc.value)
    assert "kind" in msg


def test_invalid_subject_material_rejected():
    with pytest.raises(ValidationError) as exc:
        ProductSubjectIR(kind="bottle", color="amber", material="velvet")
    msg = str(exc.value)
    assert "material" in msg


def test_invalid_schema_version_rejected():
    with pytest.raises(ValidationError):
        ProductRenderIntent(
            schema_version="v1",  # type: ignore[arg-type]
            subject=ProductSubjectIR(kind="box", color="white", material="matte"),
            backdrop=BackdropIR(color="black"),
        )


# ---------------------------------------------------------------------------
# Validation des couleurs
# ---------------------------------------------------------------------------

def test_invalid_color_name_rejected():
    with pytest.raises(ValidationError) as exc:
        ProductSubjectIR(kind="bottle", color="amber-tinted", material="glass")
    msg = str(exc.value).lower()
    assert "color" in msg or "amber-tinted" in msg


def test_invalid_hex_too_short_rejected():
    with pytest.raises(ValidationError):
        ProductSubjectIR(kind="bottle", color="#abc", material="glass")


def test_invalid_hex_with_extra_chars_rejected():
    with pytest.raises(ValidationError):
        BackdropIR(color="#1234567")


def test_empty_color_rejected():
    with pytest.raises(ValidationError):
        BackdropIR(color="")


def test_color_normalization_lowercase():
    """Les couleurs nommées sont normalisées en minuscules."""
    s = ProductSubjectIR(kind="bottle", color="AMBER", material="glass")
    assert s.color == "amber"


def test_color_hex_normalization_lowercase():
    """Les codes hex sont normalisés en minuscules."""
    s = ProductSubjectIR(kind="bottle", color="#FF00AA", material="glass")
    assert s.color == "#ff00aa"


# ---------------------------------------------------------------------------
# extra="forbid" : pas de champs hors schéma V0
# ---------------------------------------------------------------------------

def test_extra_field_on_subject_rejected():
    """V0 = 5 champs leaf. Aucun champ supplémentaire n'est accepté."""
    with pytest.raises(ValidationError):
        ProductSubjectIR(  # type: ignore[call-arg]
            kind="bottle", color="amber", material="glass",
            height_m=0.20,  # champ V1 non encore autorisé
        )


def test_extra_field_on_backdrop_rejected():
    with pytest.raises(ValidationError):
        BackdropIR(color="white", kind="curved")  # type: ignore[call-arg]


def test_extra_field_on_intent_rejected():
    with pytest.raises(ValidationError):
        ProductRenderIntent(  # type: ignore[call-arg]
            schema_version="v0",
            subject=ProductSubjectIR(kind="bottle", color="amber", material="glass"),
            backdrop=BackdropIR(color="neutral_gray"),
            camera={"lens": 50},  # camera n'est pas exposée en V0
        )


# ---------------------------------------------------------------------------
# Champs manquants
# ---------------------------------------------------------------------------

def test_missing_schema_version_rejected():
    with pytest.raises(ValidationError):
        ProductRenderIntent(  # type: ignore[call-arg]
            subject=ProductSubjectIR(kind="bottle", color="amber", material="glass"),
            backdrop=BackdropIR(color="neutral_gray"),
        )


def test_missing_subject_color_rejected():
    with pytest.raises(ValidationError):
        ProductSubjectIR(kind="bottle", material="glass")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# Palette
# ---------------------------------------------------------------------------

def test_named_color_palette_contains_required_v0_entries():
    """Garde-fou : la palette V0 doit contenir les couleurs canoniques de
    référence (amber + neutral_gray) utilisées dans la probe et l'ADR."""
    assert "amber" in NAMED_COLOR_PALETTE
    assert "neutral_gray" in NAMED_COLOR_PALETTE
    assert "white" in NAMED_COLOR_PALETTE
    assert "black" in NAMED_COLOR_PALETTE


def test_named_color_palette_entries_are_rgba_tuples():
    """Chaque entrée doit être un tuple (r, g, b, a) avec a == 1.0."""
    for name, rgba in NAMED_COLOR_PALETTE.items():
        assert len(rgba) == 4, f"{name} should be 4-tuple"
        r, g, b, a = rgba
        for c in (r, g, b):
            assert 0.0 <= c <= 1.0, f"{name} channel {c} out of [0,1]"
        assert a == 1.0, f"{name} alpha should be 1.0"


# ---------------------------------------------------------------------------
# resolve_color (pour le builder)
# ---------------------------------------------------------------------------

class TestResolveColor:

    def test_resolve_named_amber(self):
        rgba = resolve_color("amber")
        assert rgba == NAMED_COLOR_PALETTE["amber"]

    def test_resolve_named_case_insensitive(self):
        rgba = resolve_color("AMBER")
        assert rgba == NAMED_COLOR_PALETTE["amber"]

    def test_resolve_hex_lowercase(self):
        rgba = resolve_color("#ff00aa")
        assert rgba == (1.0, 0.0, 170.0 / 255.0, 1.0)

    def test_resolve_hex_uppercase(self):
        rgba = resolve_color("#FF00AA")
        assert rgba == (1.0, 0.0, 170.0 / 255.0, 1.0)

    def test_resolve_invalid_raises(self):
        with pytest.raises(ValueError):
            resolve_color("not-a-color")

    def test_resolve_empty_raises(self):
        with pytest.raises(ValueError):
            resolve_color("")


# ---------------------------------------------------------------------------
# _validate_color_token (utilitaire pur)
# ---------------------------------------------------------------------------

def test_validate_color_token_strips_whitespace_implicitly():
    """L'utilitaire strip() avant validation pour tolérer un espace LLM."""
    assert _validate_color_token("  amber  ") == "amber"


def test_validate_color_token_non_str_rejected():
    with pytest.raises(ValueError):
        _validate_color_token(42)  # type: ignore[arg-type]
