"""
Tests — H.4.1 : câblage creative_intent → template_used → manifest.json.

Vérifie :
- select_template_from_intent() : sélection via ArtisticIntent et dict équivalent
- get_template_name_from_intent() : nom du template via intent
- build_blender_script() : template_used renseigné dans BlenderRequest
- build_blender_script() : fallback message brut quand l'intent est muet
- build_blender_manifest() : future.template_used reflète request.template_used
- non-régression : sélection par message brut inchangée
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.clients.blender_client import build_blender_script
from app.engine.artifact_manifest import build_blender_manifest
from app.engine.artistic_intent import ArtisticIntent
from app.engine.blender_templates import (
    TEMPLATE_INTERIOR_SPACE,
    get_template_name,
    get_template_name_from_intent,
    select_template,
    select_template_from_intent,
)
from app.engine.blender_types import BlenderRequest, BlenderResult


# ---------------------------------------------------------------------------
# Fixtures partagées
# ---------------------------------------------------------------------------

_FAKE_ID = "test-h4-templates-001"
_FAKE_DIR = f"outputs/blender/{_FAKE_ID}"


def _make_result_success(template_used: str | None = None) -> tuple[BlenderRequest, BlenderResult]:
    req = BlenderRequest(
        request_id=_FAKE_ID,
        script_content="import bpy",
        script_path=f"{_FAKE_DIR}/scene.py",
        output_path=f"{_FAKE_DIR}/scene.blend",
        render_path=f"{_FAKE_DIR}/preview.png",
        output_dir=_FAKE_DIR,
        timeout=60,
        source_prompt="prompt",
        creative_intent={"medium": "3d_scene"},
        template_used=template_used,
    )
    res = BlenderResult(
        status="success",
        request_id=_FAKE_ID,
        script_path=req.script_path,
        output_path=req.output_path,
        render_path=None,
        output_dir=_FAKE_DIR,
        returncode=0,
        stdout=None,
        stderr=None,
        error=None,
    )
    return req, res


# ---------------------------------------------------------------------------
# Tests — select_template_from_intent / get_template_name_from_intent
# ---------------------------------------------------------------------------

class TestSelectTemplateFromIntent:

    def test_interior_scene_via_intent_returns_template(self):
        intent = ArtisticIntent(medium="3d_scene", subject_main="laboratoire")
        assert select_template_from_intent(intent) is TEMPLATE_INTERIOR_SPACE
        assert get_template_name_from_intent(intent) == "interior_space"

    def test_interior_scene_via_dict_intent_returns_template(self):
        intent = {"medium": "3d_scene", "subject_main": "salle"}
        assert select_template_from_intent(intent) is TEMPLATE_INTERIOR_SPACE
        assert get_template_name_from_intent(intent) == "interior_space"

    @pytest.mark.parametrize("subject", [
        "laboratoire", "salle", "bureau", "room",
        "salon", "cuisine", "chambre", "couloir", "hangar",
    ])
    def test_interior_subjects_match(self, subject):
        intent = ArtisticIntent(medium="3d_scene", subject_main=subject)
        assert get_template_name_from_intent(intent) == "interior_space"

    def test_non_3d_scene_medium_returns_none(self):
        intent = ArtisticIntent(medium="animation", subject_main="laboratoire")
        assert select_template_from_intent(intent) is None
        assert get_template_name_from_intent(intent) is None

    def test_product_render_medium_returns_none(self):
        intent = ArtisticIntent(medium="product_render", subject_main="bouteille")
        assert select_template_from_intent(intent) is None

    def test_unknown_medium_returns_none(self):
        intent = ArtisticIntent(medium="unknown")
        assert select_template_from_intent(intent) is None

    def test_3d_scene_with_non_interior_subject_returns_none(self):
        intent = ArtisticIntent(medium="3d_scene", subject_main="arbre")
        assert select_template_from_intent(intent) is None
        assert get_template_name_from_intent(intent) is None

    def test_none_intent_returns_none(self):
        assert select_template_from_intent(None) is None
        assert get_template_name_from_intent(None) is None

    def test_empty_dict_intent_returns_none(self):
        assert select_template_from_intent({}) is None
        assert get_template_name_from_intent({}) is None


# ---------------------------------------------------------------------------
# Tests — non-régression sélection par message brut
# ---------------------------------------------------------------------------

class TestSelectTemplateByMessageStillWorks:

    def test_message_with_interior_keyword_still_matches(self):
        assert get_template_name("crée un bureau simple") == "interior_space"
        assert select_template("crée un bureau simple") is TEMPLATE_INTERIOR_SPACE

    def test_message_without_keyword_still_returns_none(self):
        assert get_template_name("crée une sphère bleue") is None
        assert select_template("crée une sphère bleue") is None


# ---------------------------------------------------------------------------
# Tests — build_blender_script renseigne template_used
# ---------------------------------------------------------------------------

class TestBuildBlenderScriptTemplateUsed:

    def test_template_used_set_when_intent_matches(self):
        """Intent 3d_scene + laboratoire → template_used = 'interior_space'."""
        message = (
            "Créer une scène 3D de laboratoire futuriste abandonné, ambiance froide, "
            "lumière bleue d'urgence, caméra large."
        )
        with (
            patch("app.clients.blender_client.generate_with_ollama",
                  return_value="```python\nimport bpy\n```"),
            patch("app.clients.blender_client.write_intent_json", return_value=None),
            patch("pathlib.Path.mkdir"),
            patch("pathlib.Path.write_text"),
        ):
            request = build_blender_script(message=message, context={}, request_id=_FAKE_ID)

        assert request.template_used == "interior_space"
        assert request.creative_intent is not None

    def test_template_used_falls_back_to_message_when_intent_silent(self):
        """Intent muet (sujet inconnu) mais message contient 'bureau' → fallback message → interior_space."""
        # Le message "bureau simple" n'extraira pas de subject_main "bureau"
        # (pas dans _SUBJECT_RULES), donc l'intent ne matchera pas → fallback message brut.
        message = "bureau simple"
        with (
            patch("app.clients.blender_client.generate_with_ollama",
                  return_value="```python\nimport bpy\n```"),
            patch("app.clients.blender_client.write_intent_json", return_value=None),
            patch("pathlib.Path.mkdir"),
            patch("pathlib.Path.write_text"),
        ):
            request = build_blender_script(message=message, context={}, request_id=_FAKE_ID)

        assert request.template_used == "interior_space"

    def test_template_used_none_when_no_template_matches(self):
        """Prompt sans aucun signal d'intérieur → template_used reste None."""
        message = "crée une sphère bleue simple"
        with (
            patch("app.clients.blender_client.generate_with_ollama",
                  return_value="```python\nimport bpy\n```"),
            patch("app.clients.blender_client.write_intent_json", return_value=None),
            patch("pathlib.Path.mkdir"),
            patch("pathlib.Path.write_text"),
        ):
            request = build_blender_script(message=message, context={}, request_id=_FAKE_ID)

        assert request.template_used is None


# ---------------------------------------------------------------------------
# Tests — manifest.future.template_used
# ---------------------------------------------------------------------------

class TestManifestFutureTemplateUsed:

    def test_manifest_template_used_filled_when_request_has_template(self):
        req, res = _make_result_success(template_used="interior_space")
        manifest = build_blender_manifest(req, res)
        assert manifest["future"]["template_used"] == "interior_space"

    def test_manifest_template_used_null_when_request_has_none(self):
        req, res = _make_result_success(template_used=None)
        manifest = build_blender_manifest(req, res)
        assert manifest["future"]["template_used"] is None

    def test_manifest_template_used_null_on_legacy_request_without_field(self):
        """Sécurité : un objet request-like sans l'attribut doit donner None."""

        class LegacyRequest:
            request_id = _FAKE_ID
            output_dir = _FAKE_DIR
            source_prompt = "x"
            creative_intent = None
            # pas d'attribut template_used → getattr renvoie None

        res = BlenderResult(
            status="success",
            request_id=_FAKE_ID,
            script_path=None,
            output_path=None,
            render_path=None,
            output_dir=_FAKE_DIR,
            returncode=0,
            stdout=None,
            stderr=None,
            error=None,
        )
        manifest = build_blender_manifest(LegacyRequest(), res)
        assert manifest["future"]["template_used"] is None
