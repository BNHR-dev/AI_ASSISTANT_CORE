"""
H.6.4 — Tests des normalizers déterministes de product_render_extractor.

Couvre :
1. Les 3 normalizers atomiques (couleurs, material/transparency, schema_version),
   testés isolément en tant que fonctions pures.
2. Les 3 patterns d'erreur observés dans le benchmark H.6.3, vérifiés bout-en-bout
   via `parse_product_render_intent_from_text` :
   - sur-promotion v1 sur cas V0
   - "opaque" mis dans `subject.material` au lieu de `subject.transparency`
   - hex `#ffffff` / `#f5deb3` au lieu des noms `white` / `beige`
3. Les invariants : idempotence, intactness des champs non concernés,
   pas de régression sur les cas déjà parfaits.

Pure : aucun appel LLM. Le harness, le runner, et l'IR ne sont pas touchés.
"""
from __future__ import annotations

import json

import pytest

from app.engine.product_render_extractor import (
    _apply_normalizers,
    _HEX_TO_PALETTE_SYNONYMS,
    _normalize_color_hex_to_palette,
    _normalize_colors,
    _normalize_material_transparency,
    _normalize_schema_version,
    parse_product_render_intent_from_text,
)


# ===========================================================================
# 1. Normalizer couleurs
# ===========================================================================

class TestNormalizeColorHexToPalette:

    @pytest.mark.parametrize("hex_in,palette_out", [
        ("#ffffff", "white"),
        ("#FFFFFF", "white"),
        ("#fff",    "white"),
        ("#000000", "black"),
        ("#000",    "black"),
        ("#f5deb3", "beige"),
        ("#F5DEB3", "beige"),
        ("#808080", "neutral_gray"),
    ])
    def test_known_synonyms_mapped(self, hex_in, palette_out):
        assert _normalize_color_hex_to_palette(hex_in) == palette_out

    def test_unknown_hex_passes_through(self):
        # #c0c0c0 (silver) n'est pas dans la palette → on ne le force pas.
        assert _normalize_color_hex_to_palette("#c0c0c0") == "#c0c0c0"

    def test_palette_name_passes_through(self):
        assert _normalize_color_hex_to_palette("amber") == "amber"

    def test_non_string_passes_through(self):
        assert _normalize_color_hex_to_palette(None) is None
        assert _normalize_color_hex_to_palette(42) == 42

    def test_whitespace_handled(self):
        assert _normalize_color_hex_to_palette("  #ffffff  ") == "white"


class TestNormalizeColors:

    def test_subject_and_backdrop_both_normalized(self):
        data = {
            "subject": {"color": "#ffffff", "kind": "bottle"},
            "backdrop": {"color": "#f5deb3"},
        }
        out = _normalize_colors(data)
        assert out["subject"]["color"] == "white"
        assert out["backdrop"]["color"] == "beige"
        # Autres champs intacts.
        assert out["subject"]["kind"] == "bottle"

    def test_missing_subject_or_backdrop_no_crash(self):
        assert _normalize_colors({}) == {}
        assert _normalize_colors({"subject": "not a dict"}) == {"subject": "not a dict"}

    def test_no_mutation_of_input(self):
        data = {"subject": {"color": "#ffffff"}, "backdrop": {"color": "#000000"}}
        before = json.dumps(data, sort_keys=True)
        _normalize_colors(data)
        assert json.dumps(data, sort_keys=True) == before


# ===========================================================================
# 2. Normalizer material / transparency
# ===========================================================================

class TestNormalizeMaterialTransparency:

    def test_opaque_in_material_is_swapped(self):
        data = {"subject": {"kind": "tube", "color": "red", "material": "opaque"}}
        out = _normalize_material_transparency(data)
        assert out["subject"]["material"] == "matte"
        assert out["subject"]["transparency"] == "opaque"

    def test_translucent_in_material_is_swapped(self):
        data = {"subject": {"kind": "jar", "color": "white", "material": "translucent"}}
        out = _normalize_material_transparency(data)
        assert out["subject"]["material"] == "matte"
        assert out["subject"]["transparency"] == "translucent"

    def test_glass_in_material_is_left_alone(self):
        # "glass" est légal des deux côtés ; on ne suppose pas une erreur.
        data = {"subject": {"kind": "bottle", "color": "amber", "material": "glass"}}
        out = _normalize_material_transparency(data)
        assert out["subject"]["material"] == "glass"
        assert out["subject"].get("transparency") is None

    def test_existing_transparency_preserved_material_still_corrected(self):
        # H.6.4 — material="opaque" est *illégal* (Pydantic rejette). On le
        # corrige systématiquement à "matte", même si transparency est déjà
        # set : la valeur de transparency existante est préservée telle quelle.
        data = {
            "subject": {
                "kind": "tube", "color": "red",
                "material": "opaque", "transparency": "translucent",
            }
        }
        out = _normalize_material_transparency(data)
        assert out["subject"]["material"] == "matte"            # corrigé
        assert out["subject"]["transparency"] == "translucent"  # préservé

    def test_duplicated_transparency_value_in_material_corrected(self):
        # Pattern observé H.6.4 : le LLM met la même valeur dans material ET
        # transparency. material doit être corrigé, transparency préservé.
        data = {
            "subject": {
                "kind": "jar", "color": "white",
                "material": "translucent", "transparency": "translucent",
            }
        }
        out = _normalize_material_transparency(data)
        assert out["subject"]["material"] == "matte"
        assert out["subject"]["transparency"] == "translucent"

    def test_valid_material_untouched(self):
        data = {"subject": {"kind": "box", "color": "red", "material": "matte"}}
        assert _normalize_material_transparency(data) == data

    def test_missing_subject_no_crash(self):
        assert _normalize_material_transparency({}) == {}

    def test_idempotent(self):
        data = {"subject": {"kind": "tube", "color": "red", "material": "opaque"}}
        once = _normalize_material_transparency(data)
        twice = _normalize_material_transparency(once)
        assert once == twice


# ===========================================================================
# 3. Normalizer schema_version
# ===========================================================================

class TestNormalizeSchemaVersion:

    def test_v1_without_v1_fields_coerced_to_v0(self):
        data = {
            "schema_version": "v1",
            "subject": {"kind": "bottle", "color": "amber", "material": "glass"},
            "backdrop": {"color": "neutral_gray"},
        }
        out = _normalize_schema_version(data)
        assert out["schema_version"] == "v0"
        # Pas de champs V1 traînants.
        assert "shape" not in out["subject"]
        assert "cap" not in out["subject"]
        assert "transparency" not in out["subject"]
        assert "framing" not in out

    def test_v1_with_shape_kept_as_v1(self):
        data = {
            "schema_version": "v1",
            "subject": {
                "kind": "bottle", "color": "amber",
                "material": "glass", "shape": "rectangular",
            },
            "backdrop": {"color": "neutral_gray"},
        }
        out = _normalize_schema_version(data)
        assert out["schema_version"] == "v1"
        assert out["subject"]["shape"] == "rectangular"

    def test_v1_with_only_framing_kept_as_v1(self):
        data = {
            "schema_version": "v1",
            "subject": {"kind": "bottle", "color": "amber", "material": "glass"},
            "backdrop": {"color": "neutral_gray"},
            "framing": "close_packshot",
        }
        out = _normalize_schema_version(data)
        assert out["schema_version"] == "v1"
        assert out["framing"] == "close_packshot"

    def test_v0_left_alone(self):
        data = {
            "schema_version": "v0",
            "subject": {"kind": "bottle", "color": "amber", "material": "glass"},
            "backdrop": {"color": "neutral_gray"},
        }
        assert _normalize_schema_version(data) == data

    def test_v1_fields_explicitly_null_treated_as_absent(self):
        data = {
            "schema_version": "v1",
            "subject": {
                "kind": "bottle", "color": "amber", "material": "glass",
                "shape": None, "cap": None, "transparency": None,
            },
            "backdrop": {"color": "neutral_gray"},
            "framing": None,
        }
        out = _normalize_schema_version(data)
        # Aucun signal V1 effectif → coercition v0 + purge.
        assert out["schema_version"] == "v0"
        for k in ("shape", "cap", "transparency"):
            assert k not in out["subject"]
        assert "framing" not in out

    def test_idempotent(self):
        data = {
            "schema_version": "v1",
            "subject": {"kind": "bottle", "color": "amber", "material": "glass"},
            "backdrop": {"color": "neutral_gray"},
        }
        once = _normalize_schema_version(data)
        twice = _normalize_schema_version(once)
        assert once == twice


# ===========================================================================
# 4. _apply_normalizers — pipeline complet
# ===========================================================================

class TestApplyNormalizersPipeline:

    def test_full_pipeline_v0_canonical(self):
        # Cas H.6.3 v0-bottle : le modèle dit v1 sans champ V1.
        raw = {
            "schema_version": "v1",
            "subject": {"kind": "bottle", "color": "amber", "material": "glass"},
            "backdrop": {"color": "neutral_gray"},
        }
        out = _apply_normalizers(raw)
        assert out["schema_version"] == "v0"
        assert "framing" not in out

    def test_full_pipeline_material_swap_then_v1_kept(self):
        # Cas H.6.3 v1-tube : material="opaque" → swap fait apparaître
        # transparency, donc le cas reste v1 légitime après pipeline.
        raw = {
            "schema_version": "v1",
            "subject": {"kind": "tube", "color": "red", "material": "opaque"},
            "backdrop": {"color": "warm_gray"},
        }
        out = _apply_normalizers(raw)
        assert out["schema_version"] == "v1"
        assert out["subject"]["material"] == "matte"
        assert out["subject"]["transparency"] == "opaque"

    def test_full_pipeline_colors_normalized(self):
        # Cas H.6.3 v1-jar : hex au lieu de palette.
        raw = {
            "schema_version": "v1",
            "subject": {
                "kind": "jar", "color": "#ffffff",
                "material": "matte", "shape": "rounded",
                "transparency": "translucent",
            },
            "backdrop": {"color": "#f5deb3"},
            "framing": "medium",
        }
        out = _apply_normalizers(raw)
        assert out["subject"]["color"] == "white"
        assert out["backdrop"]["color"] == "beige"

    def test_pipeline_does_not_mutate_input(self):
        raw = {
            "schema_version": "v1",
            "subject": {"kind": "bottle", "color": "amber", "material": "glass"},
            "backdrop": {"color": "neutral_gray"},
        }
        snapshot = json.dumps(raw, sort_keys=True)
        _apply_normalizers(raw)
        assert json.dumps(raw, sort_keys=True) == snapshot

    def test_pipeline_idempotent(self):
        raw = {
            "schema_version": "v1",
            "subject": {"kind": "tube", "color": "#ffffff", "material": "opaque"},
            "backdrop": {"color": "#f5deb3"},
        }
        once = _apply_normalizers(raw)
        twice = _apply_normalizers(once)
        assert once == twice


# ===========================================================================
# 5. Régression bout-en-bout via le parser réel
# ===========================================================================

class TestParserRegressionH63:
    """Reproduit les 3 modes d'erreur observés sur qwen2.5-coder:7b en H.6.3
    et vérifie qu'ils sont maintenant absorbés par la normalisation."""

    def test_h63_v0_overpromoted_to_v1(self):
        # Sortie LLM représentative : v1 mais sans aucun champ V1.
        llm_out = json.dumps({
            "schema_version": "v1",
            "subject": {"kind": "bottle", "color": "amber", "material": "glass"},
            "backdrop": {"color": "neutral_gray"},
        })
        result = parse_product_render_intent_from_text(llm_out)
        assert result.status == "parsed"
        assert result.intent.schema_version == "v0"
        # Champs V1 absents.
        assert result.intent.subject.shape is None
        assert result.intent.framing is None

    def test_h63_opaque_in_material_hard_fail_recovered(self):
        # En H.6.3 ce JSON provoquait pydantic_validation_error → fallback.
        llm_out = json.dumps({
            "schema_version": "v1",
            "subject": {
                "kind": "tube",
                "color": "red",
                "material": "opaque",       # ← erreur LLM observée
                "shape": "cylindrical",
            },
            "backdrop": {"color": "warm_gray"},
        })
        result = parse_product_render_intent_from_text(llm_out)
        assert result.status == "parsed", f"expected parsed, got {result.error}"
        assert result.intent.subject.material == "matte"
        assert result.intent.subject.transparency == "opaque"
        assert result.intent.subject.shape == "cylindrical"

    def test_h63_hex_white_and_beige_normalized(self):
        # En H.6.3 : color=#ffffff, backdrop=#f5deb3 → mismatch eval.
        llm_out = json.dumps({
            "schema_version": "v1",
            "subject": {
                "kind": "jar",
                "color": "#ffffff",
                "material": "matte",
                "shape": "rounded",
                "transparency": "translucent",
            },
            "backdrop": {"color": "#f5deb3"},
            "framing": "medium",
        })
        result = parse_product_render_intent_from_text(llm_out)
        assert result.status == "parsed"
        assert result.intent.subject.color == "white"
        assert result.intent.backdrop.color == "beige"

    def test_already_correct_v0_untouched(self):
        # Pas de régression sur un cas LLM parfait.
        llm_out = json.dumps({
            "schema_version": "v0",
            "subject": {"kind": "bottle", "color": "amber", "material": "glass"},
            "backdrop": {"color": "neutral_gray"},
        })
        result = parse_product_render_intent_from_text(llm_out)
        assert result.status == "parsed"
        assert result.intent.schema_version == "v0"
        assert result.intent.subject.color == "amber"

    def test_already_correct_v1_untouched(self):
        llm_out = json.dumps({
            "schema_version": "v1",
            "subject": {
                "kind": "bottle", "color": "amber", "material": "glass",
                "shape": "rectangular", "cap": "present", "transparency": "glass",
            },
            "backdrop": {"color": "neutral_gray"},
            "framing": "close_packshot",
        })
        result = parse_product_render_intent_from_text(llm_out)
        assert result.status == "parsed"
        assert result.intent.schema_version == "v1"
        assert result.intent.subject.shape == "rectangular"
        assert result.intent.framing == "close_packshot"

    def test_extracted_json_preserves_raw_for_diagnostic(self):
        # `extracted_json` doit refléter la donnée brute pré-normalisation,
        # `intent` doit refléter la donnée post-normalisation.
        llm_out = json.dumps({
            "schema_version": "v1",
            "subject": {"kind": "bottle", "color": "#ffffff", "material": "glass"},
            "backdrop": {"color": "neutral_gray"},
        })
        result = parse_product_render_intent_from_text(llm_out)
        assert result.extracted_json["subject"]["color"] == "#ffffff"  # raw
        assert result.intent.subject.color == "white"                  # normalisé
        # Sur-promotion v1 absorbée :
        assert result.extracted_json["schema_version"] == "v1"
        assert result.intent.schema_version == "v0"


# ===========================================================================
# 6. Sanity : la table de synonymes contient bien les hex observés
# ===========================================================================

class TestHexSynonymsTableSanity:

    def test_h63_observed_hex_are_mapped(self):
        for h, name in [("#ffffff", "white"), ("#f5deb3", "beige")]:
            assert _HEX_TO_PALETTE_SYNONYMS[h] == name

    def test_palette_targets_are_all_named_colors(self):
        # On ne mappe jamais vers un autre hex (boucle interdite).
        from app.engine.product_render_ir import NAMED_COLOR_PALETTE
        for target in _HEX_TO_PALETTE_SYNONYMS.values():
            assert target in NAMED_COLOR_PALETTE
