"""
Fuzzing par propriétés (Hypothesis) — chantier durcissement n°7.

Cible les fonctions PURES exposées à des entrées non maîtrisées (texte
utilisateur, sorties LLM) et vérifie leurs CONTRATS, pas des cas précis :

- task_classifier : ne lève jamais, retourne toujours une tâche connue,
  has_image force "vision", invariance à la casse et au whitespace.
- tool_selector : ne lève jamais, retour dans l'ensemble contractuel.
- parse_product_render_intent_from_text : la docstring promet « robuste à
  toute entrée » — on tient la promesse sous fuzzing (jamais d'exception,
  toujours un status contractuel, fallback jamais None).
- analyze_security_gate : ne lève jamais, status ∈ {passed, blocked},
  un code dangereux reste bloqué après padding inoffensif.
- build_visual_request_from_text : ne lève jamais, VisualRequest valide.
- repro.sha256_canonical_json : total sur le JSON (jamais d'exception),
  déterministe, insensible à l'ordre des clés.

Tier unit : borné (max_examples) et sans deadline (variance CI), tourne
dans la CI hermétique standard.
"""
from __future__ import annotations

import json

from hypothesis import given, settings, strategies as st

from app.engine import repro
from app.engine.blender_ast_guard import analyze_security_gate
from app.engine.product_render_extractor import parse_product_render_intent_from_text
from app.task_classifier import TASKS, classify_task
from app.tool_selector import select_tool

# Bornes communes : assez d'exemples pour mordre, assez court pour la CI.
FUZZ = settings(max_examples=150, deadline=None)

# Texte « utilisateur » : unicode large, y compris accents, emojis, contrôle.
user_text = st.text(max_size=400)


# ---------------------------------------------------------------------------
# task_classifier
# ---------------------------------------------------------------------------

@FUZZ
@given(message=user_text, has_image=st.booleans())
def test_classifier_total_and_contractual(message: str, has_image: bool) -> None:
    task, reason = classify_task(message, has_image)
    assert task in TASKS
    assert isinstance(reason, str) and reason


@FUZZ
@given(message=user_text)
def test_classifier_image_input_forces_vision(message: str) -> None:
    task, _reason = classify_task(message, has_image=True)
    assert task == "vision"


@FUZZ
@given(message=user_text)
def test_classifier_case_and_whitespace_invariant(message: str) -> None:
    # normalize_text est censé absorber casse et espaces périphériques : une
    # même demande ne doit pas router différemment selon la façon de taper.
    base, _ = classify_task(message)
    upper, _ = classify_task(message.upper())
    padded, _ = classify_task(f"  {message}\t\n")
    assert base == upper == padded


# ---------------------------------------------------------------------------
# tool_selector
# ---------------------------------------------------------------------------

@FUZZ
@given(message=user_text, task_type=st.sampled_from(TASKS))
def test_tool_selector_total_and_contractual(message: str, task_type: str) -> None:
    tool = select_tool(message, task_type)
    assert tool in (None, "web", "comfyui", "blender")


# ---------------------------------------------------------------------------
# parse_product_render_intent_from_text — « robuste à toute entrée »
# ---------------------------------------------------------------------------

# Trois familles d'entrées : texte brut, JSON-ish cassé, JSON valide arbitraire.
_json_values = st.recursive(
    st.none()
    | st.booleans()
    | st.integers(min_value=-(2**31), max_value=2**31)
    | st.floats(allow_nan=False, allow_infinity=False)
    | st.text(max_size=30),
    lambda children: st.lists(children, max_size=4)
    | st.dictionaries(st.text(max_size=15), children, max_size=4),
    max_leaves=12,
)
extractor_inputs = (
    st.none()
    | user_text
    | _json_values.map(lambda v: json.dumps(v, ensure_ascii=False))
    | _json_values.map(lambda v: "bla " + json.dumps(v, ensure_ascii=False) + " }{")
)


@FUZZ
@given(text=extractor_inputs)
def test_extractor_parse_never_raises_and_always_falls_back(text) -> None:
    result = parse_product_render_intent_from_text(text)
    assert result.status in ("parsed", "fallback")
    # Le contrat produit : même en fallback, un intent exploitable existe.
    assert result.intent is not None
    if result.status == "fallback":
        assert result.error


# ---------------------------------------------------------------------------
# analyze_security_gate
# ---------------------------------------------------------------------------

@FUZZ
@given(code=user_text)
def test_security_gate_total_and_contractual(code: str) -> None:
    report = analyze_security_gate(code)
    assert report["status"] in ("passed", "blocked")
    assert isinstance(report["violations"], list)
    if report["status"] == "blocked":
        assert report["violations"]


@FUZZ
@given(padding=st.text(alphabet=" \t\n#", max_size=40))
def test_security_gate_blocked_survives_innocuous_padding(padding: str) -> None:
    # Un contournement par simple habillage (espaces, commentaires) doit
    # rester bloqué — le gate audite l'AST, pas la surface du texte.
    malicious = f"{padding}\neval('x')\n{padding}"
    assert analyze_security_gate(malicious)["status"] == "blocked"


# ---------------------------------------------------------------------------
# build_visual_request_from_text
# ---------------------------------------------------------------------------

@FUZZ
@given(prompt=st.text(min_size=1, max_size=300))
def test_visual_request_total_and_contractual(prompt: str) -> None:
    from app.clients.comfyui_client import build_visual_request_from_text

    request = build_visual_request_from_text(prompt)
    assert request.quality in ("draft", "final")
    assert request.workflow_id
    assert request.width > 0 and request.height > 0
    assert request.variants_count >= 1


# ---------------------------------------------------------------------------
# repro.sha256_canonical_json
# ---------------------------------------------------------------------------

@FUZZ
@given(data=_json_values)
def test_canonical_json_total_and_deterministic(data) -> None:
    first = repro.sha256_canonical_json(data)
    assert first is not None  # total sur tout JSON générable
    assert repro.sha256_canonical_json(data) == first


@FUZZ
@given(data=st.dictionaries(st.text(max_size=15), _json_values, max_size=5))
def test_canonical_json_key_order_invariant(data: dict) -> None:
    reordered = dict(reversed(list(data.items())))
    assert repro.sha256_canonical_json(data) == repro.sha256_canonical_json(reordered)
