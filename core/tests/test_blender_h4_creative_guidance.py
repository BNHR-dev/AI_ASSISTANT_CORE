"""
Tests — H.4.3 : Creative guidance (Option C, hybride minimal).

Vérifie que :
- _build_creative_guidance() est une fonction pure, déterministe, tolérante
  (ArtisticIntent OU dict OU None), et n'utilise QUE style / mood /
  composition_lighting. Tous les autres champs sont ignorés.
- Lorsque tous les champs autorisés sont vides / "unknown" / [], la fonction
  retourne "" et build_blender_script() n'injecte AUCUN bloc dans le prompt
  (rétrocompat H.4.2 stricte).
- Lorsqu'au moins un champ autorisé est renseigné, le prompt envoyé à
  generate_with_ollama() contient le bloc guidance, et template_used n'est
  jamais modifié (interior_space / product_render / None restent stables).
- La guidance ne porte AUCUNE atteinte à la structure garantie des scaffolds.

Aucune qualité artistique subjective n'est testée — uniquement présence,
absence, stabilité et non-régression.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.clients.blender_client import (
    _build_creative_guidance,
    build_blender_script,
)
from app.engine.artistic_intent import ArtisticIntent


_FAKE_ID = "test-h4-3-guidance-001"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _capture_prompt(message: str) -> tuple[str, object]:
    """
    Exécute build_blender_script() avec generate_with_ollama mocké, et
    retourne (prompt_envoyé_au_LLM, request).
    """
    captured: dict[str, str] = {}

    def _fake_generate(model: str, prompt: str) -> str:
        captured["prompt"] = prompt
        return "```python\nimport bpy\n```"

    with (
        patch("app.clients.blender_client.generate_with_ollama",
              side_effect=_fake_generate),
        patch("app.clients.blender_client.write_intent_json", return_value=None),
        patch("pathlib.Path.mkdir"),
        patch("pathlib.Path.write_text"),
    ):
        request = build_blender_script(message=message, context={}, request_id=_FAKE_ID)

    return captured.get("prompt", ""), request


# ---------------------------------------------------------------------------
# Groupe 1 — _build_creative_guidance() : fonction pure
# ---------------------------------------------------------------------------

class TestBuildCreativeGuidanceUnit:

    def test_none_intent_returns_empty_string(self):
        assert _build_creative_guidance(None) == ""

    def test_empty_artistic_intent_returns_empty_string(self):
        """ArtisticIntent par défaut : style=[], mood=[], lighting='unknown' → ''."""
        assert _build_creative_guidance(ArtisticIntent()) == ""

    def test_empty_dict_returns_empty_string(self):
        assert _build_creative_guidance({}) == ""

    def test_all_unknown_or_empty_returns_empty_string(self):
        intent = ArtisticIntent(
            style=[],
            mood=[],
            composition_lighting="unknown",
        )
        assert _build_creative_guidance(intent) == ""

    def test_style_only_present_in_block(self):
        intent = ArtisticIntent(style=["sci-fi", "dark"])
        guidance = _build_creative_guidance(intent)
        assert guidance != ""
        assert "Style" in guidance
        assert "sci-fi" in guidance
        assert "dark" in guidance
        # mood / lighting absents : pas de ligne dédiée
        assert "Mood" not in guidance
        assert "Lighting" not in guidance

    def test_mood_only_present_in_block(self):
        intent = ArtisticIntent(mood=["tension", "mystery"])
        guidance = _build_creative_guidance(intent)
        assert "Mood" in guidance
        assert "tension" in guidance
        assert "mystery" in guidance
        assert "Style" not in guidance
        assert "Lighting" not in guidance

    def test_lighting_only_present_in_block(self):
        intent = ArtisticIntent(composition_lighting="studio")
        guidance = _build_creative_guidance(intent)
        assert "Lighting" in guidance
        assert "studio" in guidance
        assert "Style" not in guidance
        assert "Mood" not in guidance

    def test_three_fields_combined(self):
        intent = ArtisticIntent(
            style=["sci-fi"],
            mood=["tension"],
            composition_lighting="neon",
        )
        guidance = _build_creative_guidance(intent)
        assert "sci-fi" in guidance
        assert "tension" in guidance
        assert "neon" in guidance
        # Marqueurs de bloc présents
        assert "INTENTION ARTISTIQUE" in guidance
        assert "FIN INTENTION" in guidance

    def test_block_mentions_structural_invariants(self):
        """La guidance doit RAPPELER explicitement les invariants structurels."""
        intent = ArtisticIntent(style=["dark"])
        guidance = _build_creative_guidance(intent)
        # Au moins ces piliers du scaffold doivent être nommés comme intouchables
        assert "Camera" in guidance
        assert "Key_Light" in guidance
        assert "OUTPUT_BLEND_PATH" in guidance
        assert "scene.blend" in guidance
        assert "preview.png" in guidance
        assert "manifest" in guidance
        assert "scene_report" in guidance

    def test_other_fields_are_ignored(self):
        """subject_main, composition_camera, medium, user_intent… ne doivent PAS apparaître."""
        intent = ArtisticIntent(
            user_intent="le robot rouge mystérieux",
            medium="3d_scene",
            style=[],
            mood=[],
            subject_main="robot",
            subject_secondary=["arbre"],
            composition_camera="close-up",
            composition_lighting="unknown",
        )
        guidance = _build_creative_guidance(intent)
        # Tous ces champs non retenus : aucune mention attendue.
        # Comme aucun champ autorisé n'est rempli, guidance doit être "".
        assert guidance == ""

    def test_other_fields_ignored_even_when_authorized_present(self):
        intent = ArtisticIntent(
            style=["cinematic"],
            subject_main="robot",
            subject_secondary=["arbre", "vaisseau spatial"],
            composition_camera="close-up",
            user_intent="phrase libre",
        )
        guidance = _build_creative_guidance(intent)
        assert guidance != ""
        assert "cinematic" in guidance
        # Champs hors périmètre absents
        assert "robot" not in guidance
        assert "arbre" not in guidance
        assert "vaisseau" not in guidance
        assert "close-up" not in guidance
        assert "phrase libre" not in guidance

    def test_dict_intent_equivalent_to_pydantic(self):
        """Tolérance dict : même résultat qu'avec ArtisticIntent."""
        intent_dict = {
            "style": ["sci-fi"],
            "mood": ["tension"],
            "composition_lighting": "neon",
        }
        intent_obj = ArtisticIntent(
            style=["sci-fi"],
            mood=["tension"],
            composition_lighting="neon",
        )
        assert _build_creative_guidance(intent_dict) == _build_creative_guidance(intent_obj)

    def test_dict_intent_with_only_authorized_field(self):
        guidance = _build_creative_guidance({"composition_lighting": "studio"})
        assert "studio" in guidance
        assert "Lighting" in guidance

    def test_dict_intent_with_unknown_lighting_only(self):
        assert _build_creative_guidance({"composition_lighting": "unknown"}) == ""

    def test_is_deterministic(self):
        """Deux appels successifs avec le même intent donnent EXACTEMENT le même bloc."""
        intent = ArtisticIntent(
            style=["sci-fi", "dark"],
            mood=["tension"],
            composition_lighting="neon",
        )
        assert _build_creative_guidance(intent) == _build_creative_guidance(intent)


# ---------------------------------------------------------------------------
# Groupe 2 — Injection dans build_blender_script()
# ---------------------------------------------------------------------------

class TestBuildBlenderScriptInjectsGuidance:

    def test_product_render_prompt_contains_guidance_and_keeps_template(self):
        """Prompt produit clair → guidance dans le prompt LLM, template_used inchangé."""
        message = (
            "Crée un packshot produit d'une bouteille de parfum, "
            "fond neutre, éclairage studio softbox."
        )
        prompt, request = _capture_prompt(message)

        # Template inchangé par H.4.3
        assert request.template_used == "product_render"
        # Guidance injectée dans le prompt LLM
        assert "INTENTION ARTISTIQUE" in prompt
        assert "FIN INTENTION" in prompt
        # Le scaffold reste obligatoire et identifiable
        assert "SCAFFOLD DE SCÈNE OBLIGATOIRE" in prompt
        assert "FIN SCAFFOLD" in prompt
        # La guidance arrive APRÈS le scaffold et AVANT la demande utilisateur
        idx_end_scaffold = prompt.find("--- FIN SCAFFOLD ---")
        idx_guidance = prompt.find("--- INTENTION ARTISTIQUE ---")
        idx_demande = prompt.find("Demande utilisateur")
        assert idx_end_scaffold != -1
        assert idx_guidance != -1
        assert idx_demande != -1
        assert idx_end_scaffold < idx_guidance < idx_demande

    def test_interior_space_prompt_contains_guidance_and_keeps_template(self):
        """Prompt intérieur sombre → guidance présente, template_used = interior_space."""
        message = (
            "Crée une scène 3D de laboratoire futuriste abandonné, ambiance froide, "
            "lumière bleue d'urgence, caméra large."
        )
        prompt, request = _capture_prompt(message)

        assert request.template_used == "interior_space"
        assert "INTENTION ARTISTIQUE" in prompt
        assert "FIN INTENTION" in prompt
        # Le scaffold interior_space reste actif
        assert "SCAFFOLD DE SCÈNE OBLIGATOIRE" in prompt

    def test_neutral_prompt_without_intent_has_no_guidance_block(self):
        """Prompt neutre 'sphère bleue simple' → pas de guidance (rétrocompat H.4.2)."""
        message = "crée une sphère bleue simple"
        prompt, request = _capture_prompt(message)

        # template_used inchangé : None pour ce prompt
        assert request.template_used is None
        # Aucun bloc guidance
        assert "INTENTION ARTISTIQUE" not in prompt
        assert "FIN INTENTION" not in prompt

    def test_template_used_unchanged_when_guidance_active(self):
        """La présence de la guidance ne modifie JAMAIS template_used."""
        msg_product = "packshot produit d'une bouteille de parfum, lumière studio"
        msg_interior = "crée un bureau lumineux, mood tendu, lumière dramatique"

        _, req_product = _capture_prompt(msg_product)
        _, req_interior = _capture_prompt(msg_interior)

        assert req_product.template_used == "product_render"
        assert req_interior.template_used == "interior_space"

    def test_creative_intent_in_request_unchanged_by_guidance(self):
        """request.creative_intent reste le dict complet de l'intent (H.4.1/H.4.2)."""
        message = (
            "Crée un packshot produit d'une bouteille de parfum, "
            "fond neutre, éclairage studio softbox."
        )
        _, request = _capture_prompt(message)
        assert isinstance(request.creative_intent, dict)
        # Champs structurés conservés
        assert request.creative_intent.get("medium") == "product_render"

    def test_output_blend_path_marker_unchanged(self):
        """OUTPUT_BLEND_PATH reste mentionné dans le prompt système (invariant scaffold)."""
        message = "packshot bouteille de parfum studio"
        prompt, _ = _capture_prompt(message)
        assert "OUTPUT_BLEND_PATH" in prompt


# ---------------------------------------------------------------------------
# Groupe 3 — Non-régression : la guidance n'altère pas la sélection de template
# ---------------------------------------------------------------------------

class TestGuidanceDoesNotChangeTemplateSelection:

    @pytest.mark.parametrize("message,expected", [
        ("crée un bureau simple", "interior_space"),
        ("packaging", "product_render"),
        ("crée une sphère bleue", None),
    ])
    def test_existing_template_selection_preserved(self, message, expected):
        _, request = _capture_prompt(message)
        assert request.template_used == expected
