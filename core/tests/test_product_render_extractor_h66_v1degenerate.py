"""
H.6.6 — Tests de la coercition v1-dégénéré → v0 et de la présence des
hints lexicaux dans le prompt d'extraction.

Couvre :
1. Le pattern observé en H.6.5 sur v0-jar : LLM annonce v1 et remplit les
   4 champs V1 avec exactement leurs valeurs par défaut builder. Doit
   être downgrade en v0.
2. Le non-déclenchement quand seulement certains champs V1 sont présents,
   même si égaux au défaut (cas typique après `_normalize_material_
   transparency` qui ne libère qu'un seul champ).
3. La présence des hints lexicaux français dans le prompt produit par
   `build_extraction_prompt` (les corrections H.6.5 → H.6.6 reposent
   partiellement sur ces hints côté modèle).

Aucun appel LLM réel.
"""
from __future__ import annotations

import json

import pytest

from app.engine.product_render_extractor import (
    _INVALID_COLOR_SAFETY_DEFAULT,
    _is_valid_color_token,
    _normalize_color_safety_default,
    _normalize_schema_version,
    build_extraction_prompt,
    parse_product_render_intent_from_text,
)
from app.engine.product_render_ir import V1_DEFAULTS


# ===========================================================================
# 1. _normalize_schema_version — cas H.6.6 (v1 dump complet à défaut)
# ===========================================================================

class TestV1DegenerateCoercion:

    def test_full_v1_all_defaults_coerced_to_v0(self):
        """Pattern v0-jar observé H.6.5 : 4 champs V1 présents et tous au
        défaut builder → la sortie est sémantiquement équivalente à v0."""
        data = {
            "schema_version": "v1",
            "subject": {
                "kind": "jar", "color": "white", "material": "matte",
                "shape": V1_DEFAULTS["shape"],         # "cylindrical"
                "cap": V1_DEFAULTS["cap"],             # "absent"
                "transparency": V1_DEFAULTS["transparency"],  # "opaque"
            },
            "backdrop": {"color": "beige"},
            "framing": V1_DEFAULTS["framing"],         # "medium"
        }
        out = _normalize_schema_version(data)
        assert out["schema_version"] == "v0"
        # Tous les champs V1 ont été purgés pour satisfaire _enforce_v0_purity.
        for k in ("shape", "cap", "transparency"):
            assert k not in out["subject"]
        assert "framing" not in out

    def test_one_v1_field_at_default_kept_as_v1(self):
        """Présence partielle de V1, même au default, garde v1 : cas typique
        après `_normalize_material_transparency` (1 seul V1 libéré)."""
        data = {
            "schema_version": "v1",
            "subject": {
                "kind": "tube", "color": "red", "material": "matte",
                "transparency": V1_DEFAULTS["transparency"],  # "opaque"
            },
            "backdrop": {"color": "warm_gray"},
        }
        out = _normalize_schema_version(data)
        assert out["schema_version"] == "v1"
        assert out["subject"]["transparency"] == "opaque"

    def test_three_v1_at_default_one_missing_kept_as_v1(self):
        """3/4 V1 présents tous au default, framing absent → présence
        partielle, on garde v1 (signal sélectif du modèle)."""
        data = {
            "schema_version": "v1",
            "subject": {
                "kind": "jar", "color": "white", "material": "matte",
                "shape": V1_DEFAULTS["shape"],
                "cap": V1_DEFAULTS["cap"],
                "transparency": V1_DEFAULTS["transparency"],
            },
            "backdrop": {"color": "beige"},
            # framing absent
        }
        out = _normalize_schema_version(data)
        assert out["schema_version"] == "v1"

    def test_all_four_present_one_non_default_kept_as_v1(self):
        """4/4 présents mais un signal informatif (shape=rectangular ≠
        cylindrical) → garde v1."""
        data = {
            "schema_version": "v1",
            "subject": {
                "kind": "bottle", "color": "amber", "material": "glass",
                "shape": "rectangular",                          # ≠ default
                "cap": V1_DEFAULTS["cap"],
                "transparency": V1_DEFAULTS["transparency"],
            },
            "backdrop": {"color": "neutral_gray"},
            "framing": V1_DEFAULTS["framing"],
        }
        out = _normalize_schema_version(data)
        assert out["schema_version"] == "v1"
        assert out["subject"]["shape"] == "rectangular"

    def test_idempotent(self):
        data = {
            "schema_version": "v1",
            "subject": {
                "kind": "jar", "color": "white", "material": "matte",
                "shape": V1_DEFAULTS["shape"],
                "cap": V1_DEFAULTS["cap"],
                "transparency": V1_DEFAULTS["transparency"],
            },
            "backdrop": {"color": "beige"},
            "framing": V1_DEFAULTS["framing"],
        }
        once = _normalize_schema_version(data)
        twice = _normalize_schema_version(once)
        assert once == twice


# ===========================================================================
# 2. Régression bout-en-bout via le parser
# ===========================================================================

class TestParserRegressionH65:

    def test_h65_v0_jar_pattern_parsed_as_v0(self):
        """Sortie LLM observée H.6.5 sur le cas v0-jar (sans la correction
        de kind, qui dépend du prompt) : 4 V1 fields, tous au défaut.
        Après normalisation : doit être un IR V0 valide."""
        llm_out = json.dumps({
            "schema_version": "v1",
            "subject": {
                "kind": "jar", "color": "white", "material": "matte",
                "shape": "cylindrical", "cap": "absent",
                "transparency": "opaque",
            },
            "backdrop": {"color": "beige"},
            "framing": "medium",
        })
        result = parse_product_render_intent_from_text(llm_out)
        assert result.status == "parsed"
        assert result.intent.schema_version == "v0"
        assert result.intent.subject.kind == "jar"
        assert result.intent.subject.shape is None
        assert result.intent.framing is None

    def test_v1_with_explicit_non_default_preserved(self):
        """Garde-fou : sortie v1 informative reste v1."""
        llm_out = json.dumps({
            "schema_version": "v1",
            "subject": {
                "kind": "bottle", "color": "amber", "material": "glass",
                "shape": "rectangular", "cap": "present",
                "transparency": "glass",
            },
            "backdrop": {"color": "neutral_gray"},
            "framing": "close_packshot",
        })
        result = parse_product_render_intent_from_text(llm_out)
        assert result.status == "parsed"
        assert result.intent.schema_version == "v1"
        assert result.intent.subject.shape == "rectangular"


# ===========================================================================
# 3. Color safety default (H.6.6)
# ===========================================================================

class TestIsValidColorToken:

    @pytest.mark.parametrize("token", [
        "amber", "neutral_gray", "white", "#ffffff", "#a83232", "#ABCDEF",
    ])
    def test_valid_tokens(self, token):
        assert _is_valid_color_token(token) is True

    @pytest.mark.parametrize("token", [
        "chrome", "silver", "ivory", "amber-tinted", "", None, 42, "#fff",
        # NB: #fff (3-char hex) n'est pas accepté par _validate_color_token
        # qui exige #RRGGBB. C'est cohérent.
    ])
    def test_invalid_tokens(self, token):
        assert _is_valid_color_token(token) is False


class TestColorSafetyDefault:

    def test_invalid_subject_color_replaced(self):
        data = {
            "subject": {"kind": "sphere", "color": "chrome", "material": "metallic"},
            "backdrop": {"color": "cool_gray"},
        }
        out = _normalize_color_safety_default(data)
        assert out["subject"]["color"] == _INVALID_COLOR_SAFETY_DEFAULT
        # backdrop déjà valide → intact.
        assert out["backdrop"]["color"] == "cool_gray"

    def test_invalid_backdrop_color_replaced(self):
        data = {
            "subject": {"kind": "box", "color": "red", "material": "matte"},
            "backdrop": {"color": "absolutely-not-a-color"},
        }
        out = _normalize_color_safety_default(data)
        assert out["backdrop"]["color"] == _INVALID_COLOR_SAFETY_DEFAULT
        assert out["subject"]["color"] == "red"

    def test_both_invalid(self):
        data = {
            "subject": {"kind": "sphere", "color": "chrome", "material": "metallic"},
            "backdrop": {"color": "ivory"},
        }
        out = _normalize_color_safety_default(data)
        assert out["subject"]["color"] == _INVALID_COLOR_SAFETY_DEFAULT
        assert out["backdrop"]["color"] == _INVALID_COLOR_SAFETY_DEFAULT

    def test_valid_colors_untouched(self):
        data = {
            "subject": {"kind": "bottle", "color": "amber", "material": "glass"},
            "backdrop": {"color": "#a83232"},
        }
        out = _normalize_color_safety_default(data)
        assert out == data

    def test_missing_subject_no_crash(self):
        assert _normalize_color_safety_default({}) == {}
        assert _normalize_color_safety_default({"backdrop": {"color": "x"}}) \
            == {"backdrop": {"color": _INVALID_COLOR_SAFETY_DEFAULT}}

    def test_idempotent(self):
        data = {
            "subject": {"kind": "sphere", "color": "chrome", "material": "metallic"},
            "backdrop": {"color": "neutral_gray"},
        }
        once = _normalize_color_safety_default(data)
        twice = _normalize_color_safety_default(once)
        assert once == twice


class TestColorSafetyDefaultEndToEnd:
    """Reproduit le pattern v0-sphere observé dans le bench H.6.6 :
    LLM produit `color="chrome"` → sans safety default, Pydantic rejette
    → fallback → tous les champs scorés à 0. Avec safety default,
    parse_ok et les autres champs restent évaluables."""

    def test_invalid_color_no_longer_triggers_fallback(self):
        llm_out = json.dumps({
            "schema_version": "v1",
            "subject": {"kind": "sphere", "color": "chrome", "material": "metallic"},
            "backdrop": {"color": "neutral_gray"},
        })
        result = parse_product_render_intent_from_text(llm_out)
        assert result.status == "parsed"
        # color a été remplacée par le safety default.
        assert result.intent.subject.color == _INVALID_COLOR_SAFETY_DEFAULT
        # kind et material préservés.
        assert result.intent.subject.kind == "sphere"
        assert result.intent.subject.material == "metallic"
        # extracted_json reste la donnée brute pour traçabilité.
        assert result.extracted_json["subject"]["color"] == "chrome"


# ===========================================================================
# 4. Hints lexicaux dans le prompt
# ===========================================================================

class TestLexicalHintsInPrompt:
    """
    Sanity-check : les hints lexicaux français H.6.6 figurent dans le
    prompt construit. C'est la **seule** garantie côté code que les
    instructions sont envoyées au LLM ; leur **efficacité** se mesure
    via le benchmark réel, pas via les tests unitaires.
    """

    def test_prompt_contains_kind_hints(self):
        p = build_extraction_prompt("ignored")
        assert "pot" in p
        assert "kind=jar" in p
        assert "kind=bottle" in p
        assert "kind=box" in p
        assert "kind=tube" in p
        assert "kind=sphere" in p

    def test_prompt_does_not_force_backdrop_color_lexical_mapping(self):
        # H.6.6 raffiné : les hints lexicaux backdrop ont été retirés
        # car ils déstabilisaient certains cas (v0-sphere : LLM produit
        # "warm_gray" au lieu de "cool_gray" sous l'effet des hints).
        # La cohérence des couleurs reste portée par la palette + le
        # safety default. Ce test verrouille cette décision.
        p = build_extraction_prompt("ignored")
        assert "gris froid → backdrop" not in p
        assert "gris chaud → backdrop" not in p

    def test_prompt_still_carries_user_message(self):
        p = build_extraction_prompt("ma demande utilisateur unique XYZ123")
        assert "ma demande utilisateur unique XYZ123" in p

    def test_prompt_remains_under_token_safety_margin(self):
        # Sanity : le prompt + un message court devrait rester très en
        # dessous de num_ctx=4096 (cf H.6.5.a). On vérifie sur la longueur
        # caractères, approximation grossière de tokens.
        p = build_extraction_prompt("petite demande")
        assert len(p) < 8000, f"prompt trop long: {len(p)} chars"
