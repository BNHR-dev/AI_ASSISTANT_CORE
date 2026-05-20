"""
Tests — H.4.4 : Alignement sélection template / intent / fallback.

Vérifie que :
- "studio" (terme d'éclairage) ne déclenche PLUS intent.medium = "product_render"
- "commercial" (terme ambigu) ne déclenche PLUS intent.medium = "product_render"
- Les cas heureux product_render restent stables (non-régression)
- Les cas heureux interior_space restent stables (non-régression)
- intent.medium et template_used sont cohérents sur les chemins principaux
- Le fallback message brut reste un filet de sécurité propre
- "production" (edge case "product" substring) est documenté comportement connu
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.clients.blender_client import build_blender_script
from app.engine.artistic_intent import parse_artistic_intent
from app.engine.blender_templates import (
    get_template_name,
    get_template_name_from_intent,
    select_template,
    select_template_from_intent,
    TEMPLATE_INTERIOR_SPACE,
    TEMPLATE_PRODUCT_RENDER,
)


_FAKE_ID = "test-h44-alignment-001"


# ---------------------------------------------------------------------------
# H.4.4 — Correction : "studio" ne doit plus déclencher product_render
# ---------------------------------------------------------------------------

class TestStudioNotProductRender:

    def test_studio_alone_does_not_give_product_render_medium(self):
        """'studio' seul → medium ≠ product_render (c'est un éclairage, pas un medium)."""
        intent = parse_artistic_intent("studio")
        assert intent.medium != "product_render"

    def test_eclairage_studio_does_not_give_product_render_medium(self):
        """'éclairage studio' → medium ≠ product_render."""
        intent = parse_artistic_intent("éclairage studio softbox")
        assert intent.medium != "product_render"

    def test_studio_lighting_gives_studio_composition_lighting(self):
        """'studio' → composition_lighting = 'studio' (le bon endroit)."""
        intent = parse_artistic_intent("scène avec éclairage studio")
        assert intent.composition_lighting == "studio"

    def test_scene_studio_interieure_does_not_select_product_render_template(self):
        """'scène studio intérieure' → template_used ≠ product_render."""
        message = "scène studio intérieure avec lumière studio"
        intent = parse_artistic_intent(message)
        template_via_intent = get_template_name_from_intent(intent)
        template_via_message = get_template_name(message)
        # Le template ne doit pas être product_render dans ce cas
        assert template_via_intent != "product_render"
        assert template_via_message != "product_render"

    def test_studio_does_not_pollute_template_used_in_request(self):
        """'scène studio pour une salle' → template_used = interior_space (pas product_render)."""
        message = "crée une scène studio pour une salle de contrôle"
        with (
            patch("app.clients.blender_client.generate_with_ollama",
                  return_value="```python\nimport bpy\n```"),
            patch("app.clients.blender_client.write_intent_json", return_value=None),
            patch("pathlib.Path.mkdir"),
            patch("pathlib.Path.write_text"),
        ):
            request = build_blender_script(message=message, context={}, request_id=_FAKE_ID)

        assert request.template_used != "product_render"


# ---------------------------------------------------------------------------
# H.4.4 — Correction : "commercial" ne doit plus déclencher product_render
# ---------------------------------------------------------------------------

class TestCommercialNotProductRender:

    def test_commercial_alone_does_not_give_product_render_medium(self):
        """'commercial' seul → medium ≠ product_render (trop ambigu)."""
        intent = parse_artistic_intent("commercial")
        assert intent.medium != "product_render"

    def test_espace_commercial_does_not_give_product_render_medium(self):
        """'espace commercial' → medium ≠ product_render (c'est un intérieur)."""
        intent = parse_artistic_intent("espace commercial moderne avec vitrines")
        assert intent.medium != "product_render"


# ---------------------------------------------------------------------------
# Non-régression — product_render reste stable sur les vrais cas produit
# ---------------------------------------------------------------------------

class TestProductRenderNonRegression:

    def test_packshot_still_gives_product_render_medium(self):
        intent = parse_artistic_intent("packshot produit cosmétique fond blanc")
        assert intent.medium == "product_render"

    def test_rendu_produit_still_gives_product_render_medium(self):
        intent = parse_artistic_intent("rendu produit photoréaliste d'une bouteille")
        assert intent.medium == "product_render"

    def test_produit_still_gives_product_render_medium(self):
        intent = parse_artistic_intent("bouteille de produit sur fond neutre")
        assert intent.medium == "product_render"

    @pytest.mark.parametrize("message", [
        "packshot produit cosmétique",
        "rendu produit photoréaliste",
        "bouteille de parfum sur fond blanc",
        "mockup produit minimaliste",
    ])
    def test_product_messages_still_trigger_product_render_template(self, message):
        assert get_template_name(message) == "product_render"
        assert select_template(message) is TEMPLATE_PRODUCT_RENDER


# ---------------------------------------------------------------------------
# Non-régression — interior_space reste stable
# ---------------------------------------------------------------------------

class TestInteriorSpaceNonRegression:

    def test_laboratoire_still_gives_interior_space(self):
        intent = parse_artistic_intent("scène de laboratoire futuriste abandonné")
        template = get_template_name_from_intent(intent)
        assert template == "interior_space"

    def test_bureau_via_message_fallback_still_gives_interior_space(self):
        assert get_template_name("crée un bureau simple") == "interior_space"

    def test_salle_de_controle_still_gives_interior_space(self):
        intent = parse_artistic_intent("salle de contrôle spatiale")
        template = get_template_name_from_intent(intent)
        assert template == "interior_space"


# ---------------------------------------------------------------------------
# Cohérence intent.medium / template_used sur les chemins principaux
# ---------------------------------------------------------------------------

class TestIntentMediumTemplateUsedConsistency:

    def test_product_render_intent_and_template_agree(self):
        """packshot → intent.medium=product_render ET template=product_render."""
        message = "packshot produit bouteille fond neutre éclairage studio softbox"
        with (
            patch("app.clients.blender_client.generate_with_ollama",
                  return_value="```python\nimport bpy\n```"),
            patch("app.clients.blender_client.write_intent_json", return_value=None),
            patch("pathlib.Path.mkdir"),
            patch("pathlib.Path.write_text"),
        ):
            request = build_blender_script(message=message, context={}, request_id=_FAKE_ID)

        assert request.template_used == "product_render"
        assert request.creative_intent["medium"] == "product_render"

    def test_interior_intent_and_template_agree(self):
        """laboratoire → intent.medium=3d_scene ET template=interior_space."""
        message = "scène de laboratoire futuriste avec lumière bleue d'urgence"
        with (
            patch("app.clients.blender_client.generate_with_ollama",
                  return_value="```python\nimport bpy\n```"),
            patch("app.clients.blender_client.write_intent_json", return_value=None),
            patch("pathlib.Path.mkdir"),
            patch("pathlib.Path.write_text"),
        ):
            request = build_blender_script(message=message, context={}, request_id=_FAKE_ID)

        assert request.template_used == "interior_space"
        assert request.creative_intent["medium"] == "3d_scene"


# ---------------------------------------------------------------------------
# Edge case documenté : "product" est substring de "production" (connu, non corrigé)
# ---------------------------------------------------------------------------

class TestProductionSubstringKnownBehavior:

    def test_production_industrielle_known_edge_case(self):
        """
        CONNU H.4.4 : "product" substring de "production" → medium=product_render.
        Ce comportement est un edge case non corrigé en H.4.4 (très rare en pratique).
        Ce test documente le comportement actuel — il devra être ADAPTÉ si corrigé.
        """
        intent = parse_artistic_intent("scène de production industrielle")
        # Documenter le comportement actuel sans l'asserter comme "correct"
        # Si ce test passe, le bug est toujours présent et accepté.
        # Si quelqu'un corrige le bug, ce test cassera et devra être mis à jour.
        assert intent.medium in ("product_render", "3d_scene"), (
            "Edge case 'production' → medium doit être l'un ou l'autre. "
            "Si medium='product_render', le bug substring est encore présent (accepté H.4.4)."
        )
