"""
Tests de non-régression — mini-phase traçabilité post-audit Fable 5 (2026-06-10).

Couvre les trois findings de traçabilité de l'audit :
- A1 : request_id de execute_request propagé jusqu'à build_blender_script
       (le dossier outputs/blender/<id> doit correspondre au request_id API).
- A2 : manifest.scene_report reflète BlenderResult.scene_report (et plus
       jamais "unavailable" quand le rapport existe).
- A8 : intent.json conserve le message utilisateur intégral (user_message),
       en plus de la reformulation courte user_intent (cap 120 chars).
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from app.engine.artifact_manifest import build_blender_manifest
from app.engine.artistic_intent import parse_artistic_intent, write_intent_json
from app.engine.blender_types import BlenderRequest, BlenderResult


# ---------------------------------------------------------------------------
# Fixtures minimales
# ---------------------------------------------------------------------------

_FAKE_DIR = "outputs/blender/trace-fix-001"


def _make_request() -> BlenderRequest:
    return BlenderRequest(
        request_id="trace-fix-001",
        script_content="import bpy",
        script_path=f"{_FAKE_DIR}/scene.py",
        output_path=f"{_FAKE_DIR}/scene.blend",
        render_path=f"{_FAKE_DIR}/preview.png",
        output_dir=_FAKE_DIR,
        timeout=60,
    )


def _make_result(**overrides) -> BlenderResult:
    base = dict(
        status="success",
        request_id="trace-fix-001",
        script_path=f"{_FAKE_DIR}/scene.py",
        output_path=f"{_FAKE_DIR}/scene.blend",
        render_path=f"{_FAKE_DIR}/preview.png",
        output_dir=_FAKE_DIR,
        returncode=0,
        stdout=None,
        stderr=None,
        error=None,
    )
    base.update(overrides)
    return BlenderResult(**base)


# ---------------------------------------------------------------------------
# A1 — propagation request_id jusqu'au pipeline Blender
# ---------------------------------------------------------------------------

def test_a1_execute_request_propagates_request_id_to_blender():
    from app.engine.executor import execute_request

    captured: dict = {}

    def fake_build(message, context, request_id):
        captured["request_id"] = request_id
        return _make_request()

    with (
        patch("app.engine.step_executor.build_blender_script", side_effect=fake_build),
        patch("app.engine.step_executor.run_blender_script", return_value=_make_result()),
    ):
        response = execute_request("Blender bpy: crée un cube bleu simple")

    assert response["selected_tool"] == "blender"
    assert "request_id" in captured, "build_blender_script n'a pas été appelé"
    assert captured["request_id"] == response["request_id"], (
        "Le request_id passé au pipeline Blender doit être celui de la réponse API "
        "(corrélation outputs/blender/<id> ↔ API, audit A1)"
    )


# ---------------------------------------------------------------------------
# A2 — manifest.scene_report reflète le rapport réel
# ---------------------------------------------------------------------------

def test_a2_manifest_reflects_populated_scene_report():
    scene_report = {
        "status": "degraded",
        "violations": ["decor_dominates"],
        "visual_qa": {"status": "degraded"},
    }
    result = _make_result(scene_report=scene_report)

    manifest = build_blender_manifest(_make_request(), result)

    assert manifest["scene_report"]["status"] == "degraded"
    assert manifest["scene_report"]["violations"] == ["decor_dominates"]


def test_a2_manifest_meta_fallback_still_works():
    result = _make_result(
        meta={"blender_scene_report": {"status": "passed", "violations": []}},
    )
    manifest = build_blender_manifest(_make_request(), result)
    assert manifest["scene_report"]["status"] == "passed"


def test_a2_manifest_scene_report_takes_precedence_over_meta():
    result = _make_result(
        scene_report={"status": "degraded", "violations": ["x"]},
        meta={"blender_scene_report": {"status": "passed", "violations": []}},
    )
    manifest = build_blender_manifest(_make_request(), result)
    assert manifest["scene_report"]["status"] == "degraded"


def test_a2_manifest_unavailable_when_no_report_anywhere():
    manifest = build_blender_manifest(_make_request(), _make_result())
    assert manifest["scene_report"]["status"] == "unavailable"
    assert manifest["scene_report"]["violations"] == []


# ---------------------------------------------------------------------------
# A8 — intention utilisateur intégrale dans intent.json
# ---------------------------------------------------------------------------

_LONG_PROMPT = (
    "Dans Blender, crée un packshot premium d'un flacon de parfum rectangulaire "
    "en verre noir mat avec bouchon argenté, cadrage héro, fond studio neutre"
)


def test_a8_long_prompt_user_message_is_complete():
    assert len(_LONG_PROMPT) > 120  # garde-fou : le cas reproduit bien la troncature
    intent = parse_artistic_intent(_LONG_PROMPT)
    assert intent.user_message == _LONG_PROMPT


def test_a8_user_intent_short_reformulation_preserved():
    intent = parse_artistic_intent(_LONG_PROMPT)
    assert len(intent.user_intent) <= 120
    assert _LONG_PROMPT.startswith(intent.user_intent)


def test_a8_intent_json_contains_full_user_message(tmp_path: Path):
    intent = parse_artistic_intent(_LONG_PROMPT)
    written = write_intent_json(intent, str(tmp_path))
    assert written is not None
    data = json.loads(Path(written).read_text(encoding="utf-8"))
    assert data["user_message"] == _LONG_PROMPT


def test_a8_empty_message_user_message_empty():
    intent = parse_artistic_intent("   ")
    assert intent.user_message == ""
    assert intent.user_intent == ""
