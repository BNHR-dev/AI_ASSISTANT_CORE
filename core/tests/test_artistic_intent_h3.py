"""
Tests unitaires — Artistic Intent Layer V0 (H.3).

Couvre :
- parse_artistic_intent() : golden prompts + fallbacks
- write_intent_json() : écriture fichier + best-effort
- Intégration manifest : future.creative_intent non null, artifacts.intent_json
- build_blender_script() : BlenderRequest.creative_intent non null
- runtime_debug : artistic_intent.py classifié
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from app.engine.artistic_intent import ArtisticIntent, parse_artistic_intent, write_intent_json
from app.engine.artifact_manifest import build_blender_manifest
from app.engine.blender_types import BlenderRequest, BlenderResult


# ---------------------------------------------------------------------------
# Fixtures partagées
# ---------------------------------------------------------------------------

_FAKE_ID = "test-h3-intent-001"
_FAKE_DIR = f"outputs/blender/{_FAKE_ID}"
_FAKE_BLEND = f"{_FAKE_DIR}/scene.blend"
_FAKE_SCRIPT = f"{_FAKE_DIR}/scene.py"


def _make_request_with_intent(intent_dict: dict | None = None) -> BlenderRequest:
    return BlenderRequest(
        request_id=_FAKE_ID,
        script_content="import bpy",
        script_path=_FAKE_SCRIPT,
        output_path=_FAKE_BLEND,
        render_path=f"{_FAKE_DIR}/preview.png",
        output_dir=_FAKE_DIR,
        timeout=60,
        source_prompt="une sphère bleue",
        creative_intent=intent_dict,
    )


def _make_result(status: str = "success") -> BlenderResult:
    return BlenderResult(
        status=status,
        request_id=_FAKE_ID,
        script_path=_FAKE_SCRIPT,
        output_path=_FAKE_BLEND if status == "success" else None,
        render_path=None,
        output_dir=_FAKE_DIR,
        returncode=0 if status == "success" else None,
        stdout=None,
        stderr=None,
        error=None if status == "success" else f"Blender {status}",
    )


# ---------------------------------------------------------------------------
# Tests parse_artistic_intent — golden prompts
# ---------------------------------------------------------------------------

# Golden prompt 1 : labo futuriste
_GOLDEN_1 = (
    "Créer une scène 3D de laboratoire futuriste abandonné, ambiance froide, "
    "lumière bleue d'urgence, caméra large."
)

# Golden prompt 2 : île flottante
_GOLDEN_2 = (
    "Créer une petite île flottante stylisée avec arbre central, herbe, rochers "
    "et lumière douce."
)

# Golden prompt 3 : salle médiévale
_GOLDEN_3 = (
    "Créer une salle médiévale sombre avec table en bois, chandelles, coffre "
    "et lumière chaude."
)

# Golden prompt 4 : animation
_GOLDEN_4 = "Créer une animation simple d'un cube métallique qui tourne sur lui-même pendant 120 frames."

# Golden prompt 5 : product render
_GOLDEN_5 = "Créer une scène produit avec une bouteille de parfum sur socle, éclairage studio, fond minimaliste."

# Golden prompt 6 : cyberpunk
_GOLDEN_6 = "Créer une rue cyberpunk de nuit avec néons, pluie suggérée, perspective profonde."


def test_parse_golden_1_medium_3d_scene():
    intent = parse_artistic_intent(_GOLDEN_1)
    assert intent.medium == "3d_scene"


def test_parse_golden_1_lighting_blue_emergency():
    intent = parse_artistic_intent(_GOLDEN_1)
    assert intent.composition_lighting == "blue emergency"


def test_parse_golden_1_camera_wide():
    intent = parse_artistic_intent(_GOLDEN_1)
    assert intent.composition_camera == "wide"


def test_parse_golden_1_subject_laboratoire():
    intent = parse_artistic_intent(_GOLDEN_1)
    assert intent.subject_main == "laboratoire"


def test_parse_golden_1_mood_cold():
    intent = parse_artistic_intent(_GOLDEN_1)
    assert "cold" in intent.mood


def test_parse_golden_2_subject_non_empty():
    intent = parse_artistic_intent(_GOLDEN_2)
    assert intent.subject_main != ""
    assert intent.subject_main != "unknown" or intent.confidence >= 0.0


def test_parse_golden_2_lighting_soft():
    intent = parse_artistic_intent(_GOLDEN_2)
    assert intent.composition_lighting == "soft"


def test_parse_golden_3_mood_dark():
    intent = parse_artistic_intent(_GOLDEN_3)
    assert "dark" in intent.mood


def test_parse_golden_3_lighting_warm_or_candlelight():
    intent = parse_artistic_intent(_GOLDEN_3)
    assert intent.composition_lighting in ("warm", "candlelight")


def test_parse_golden_4_animation_medium():
    intent = parse_artistic_intent(_GOLDEN_4)
    assert intent.medium == "animation"


def test_parse_golden_4_subject_cube():
    intent = parse_artistic_intent(_GOLDEN_4)
    assert intent.subject_main == "cube"


def test_parse_golden_5_product_render_medium():
    intent = parse_artistic_intent(_GOLDEN_5)
    assert intent.medium == "product_render"


def test_parse_golden_5_subject_bouteille():
    intent = parse_artistic_intent(_GOLDEN_5)
    assert intent.subject_main == "bouteille"


def test_parse_golden_6_style_sci_fi_or_cyberpunk():
    intent = parse_artistic_intent(_GOLDEN_6)
    assert "sci-fi" in intent.style


def test_parse_golden_6_lighting_neon():
    intent = parse_artistic_intent(_GOLDEN_6)
    assert intent.composition_lighting == "neon"


# ---------------------------------------------------------------------------
# Tests parse_artistic_intent — fallbacks
# ---------------------------------------------------------------------------

def test_parse_fallback_empty_string():
    intent = parse_artistic_intent("")
    assert isinstance(intent, ArtisticIntent)
    assert intent.medium == "unknown"
    assert intent.style == []
    assert intent.mood == []
    assert intent.confidence == 0.0
    assert intent.workflow_target == "blender_scene_preview"


def test_parse_fallback_whitespace():
    intent = parse_artistic_intent("   ")
    assert isinstance(intent, ArtisticIntent)
    assert intent.confidence == 0.0


def test_parse_fallback_vague_prompt():
    intent = parse_artistic_intent("fais quelque chose")
    assert isinstance(intent, ArtisticIntent)
    assert intent.workflow_target == "blender_scene_preview"


def test_parse_always_returns_valid_instance():
    """Même sur un prompt aberrant, retourne toujours une ArtisticIntent valide."""
    intent = parse_artistic_intent("xyz 123 !@#$%")
    assert isinstance(intent, ArtisticIntent)


# ---------------------------------------------------------------------------
# Tests ArtisticIntent — schéma Pydantic
# ---------------------------------------------------------------------------

def test_artistic_intent_schema_valid():
    intent = parse_artistic_intent(_GOLDEN_1)
    # Doit être une instance valide Pydantic
    assert isinstance(intent, ArtisticIntent)
    data = intent.model_dump()
    # Tous les champs attendus présents
    expected_keys = {
        "user_intent", "medium", "style", "mood",
        "subject_main", "subject_secondary",
        "composition_camera", "composition_lighting",
        "workflow_target", "confidence",
    }
    assert expected_keys <= set(data.keys())


def test_artistic_intent_confidence_range():
    intent = parse_artistic_intent(_GOLDEN_1)
    assert 0.0 <= intent.confidence <= 1.0


def test_artistic_intent_workflow_target_always_blender():
    for prompt in [_GOLDEN_1, _GOLDEN_2, _GOLDEN_3, _GOLDEN_4, _GOLDEN_5, ""]:
        intent = parse_artistic_intent(prompt)
        assert intent.workflow_target == "blender_scene_preview"


# ---------------------------------------------------------------------------
# Tests write_intent_json
# ---------------------------------------------------------------------------

def test_write_intent_json_creates_file(tmp_path):
    intent = parse_artistic_intent(_GOLDEN_1)
    result_path = write_intent_json(intent, str(tmp_path))
    assert result_path is not None
    assert Path(result_path).exists()
    assert Path(result_path).name == "intent.json"


def test_write_intent_json_content_valid(tmp_path):
    intent = parse_artistic_intent(_GOLDEN_1)
    result_path = write_intent_json(intent, str(tmp_path))
    content = json.loads(Path(result_path).read_text(encoding="utf-8"))
    assert "medium" in content
    assert "workflow_target" in content
    assert content["workflow_target"] == "blender_scene_preview"
    assert "confidence" in content


def test_write_intent_json_non_blocking_on_error(tmp_path):
    """Une erreur d'écriture ne doit pas crasher le pipeline."""
    intent = parse_artistic_intent(_GOLDEN_1)
    with patch("app.engine.artistic_intent.Path.write_text", side_effect=OSError("disk full")):
        result = write_intent_json(intent, str(tmp_path))
    assert result is None  # échec non bloquant → None


# ---------------------------------------------------------------------------
# Tests intégration manifest — future.creative_intent
# ---------------------------------------------------------------------------

def test_manifest_future_creative_intent_non_null_when_provided():
    """Quand BlenderRequest.creative_intent est fourni, le manifest le propage."""
    fake_intent = {"medium": "3d_scene", "workflow_target": "blender_scene_preview"}
    request = _make_request_with_intent(fake_intent)
    manifest = build_blender_manifest(request, _make_result())
    assert manifest["future"]["creative_intent"] is not None
    assert manifest["future"]["creative_intent"]["medium"] == "3d_scene"


def test_manifest_future_creative_intent_null_when_absent():
    """Le comportement H.1 est préservé : sans creative_intent, le manifest garde None."""
    request = _make_request_with_intent(None)
    manifest = build_blender_manifest(request, _make_result())
    assert manifest["future"]["creative_intent"] is None


def test_manifest_artifacts_intent_json_key_present():
    """artifacts.intent_json est présent dans le manifest."""
    request = _make_request_with_intent(None)
    manifest = build_blender_manifest(request, _make_result())
    assert "intent_json" in manifest["artifacts"]


def test_manifest_artifacts_intent_json_has_path_and_exists():
    """artifacts.intent_json contient les clés path et exists."""
    request = _make_request_with_intent(None)
    manifest = build_blender_manifest(request, _make_result())
    entry = manifest["artifacts"]["intent_json"]
    assert "path" in entry
    assert "exists" in entry


# ---------------------------------------------------------------------------
# Tests build_blender_script — creative_intent propagé à BlenderRequest
# ---------------------------------------------------------------------------

def test_blender_request_has_creative_intent():
    """
    build_blender_script() doit retourner un BlenderRequest avec creative_intent non null.
    On mock generate_with_ollama, select_template, write_intent_json et mkdir.
    """
    from app.clients.blender_client import build_blender_script

    with (
        patch("app.clients.blender_client.generate_with_ollama", return_value="```python\nimport bpy\n```"),
        patch("app.clients.blender_client.select_template", return_value=None),
        patch("app.clients.blender_client.get_template_name", return_value=None),
        patch("app.clients.blender_client.write_intent_json", return_value=None),
        patch("pathlib.Path.mkdir"),
        patch("pathlib.Path.write_text"),
    ):
        request = build_blender_script(
            message=_GOLDEN_1,
            context={},
            request_id=_FAKE_ID,
        )

    assert request.creative_intent is not None
    assert isinstance(request.creative_intent, dict)
    assert "medium" in request.creative_intent
    assert "workflow_target" in request.creative_intent


# ---------------------------------------------------------------------------
# Tests runtime_debug — artistic_intent.py classifié
# ---------------------------------------------------------------------------

def test_runtime_debug_lists_artistic_intent():
    from app.engine.runtime_debug import ACTIVE_AUXILIARY_MODULES
    assert "app/engine/artistic_intent.py" in ACTIVE_AUXILIARY_MODULES
