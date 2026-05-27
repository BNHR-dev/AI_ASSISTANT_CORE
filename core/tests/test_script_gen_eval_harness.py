"""
H.6.8.a — Tests du harness `script_gen_eval_harness`.

Vérifie le scoring déterministe sur des `raw_response` mockés :
- script bien formé pour freeform → score haut, ast_parseable=True.
- script vide → generation_ok=False, score=0.0.
- script non parsable → ast_parseable=False, score=0.0.
- script template-conformant → template_required_objects_named complet.
- script template manquant un objet → ce détail apparaît, score baisse.

Tests purs : `generate_fn` toujours mocké. Aucun appel Ollama réel.
Aucun subprocess Blender. Lecture seule sur AST guard / templates.
"""
from __future__ import annotations

import pytest

from app.engine.blender_templates import TEMPLATE_SPECS
from app.engine.script_gen_eval_cases import (
    CHECK_AST_PARSEABLE,
    CHECK_DELETE_DEFAULT_PRESENT,
    CHECK_GENERATION_OK,
    CHECK_HAS_PRIMITIVE_GEOMETRY,
    CHECK_NOT_FALLBACK_CUBE_SUN_ONLY,
    CHECK_TEMPLATE_REQUIRED_OBJECTS,
    DEFAULT_CASES,
    ScriptGenCase,
)
from app.engine.script_gen_eval_harness import (
    case_score_to_dict,
    report_to_dict,
    resolve_template_for_message,
    run_harness,
    score_script,
)


# ---------------------------------------------------------------------------
# Fixtures script samples (mocked LLM outputs)
# ---------------------------------------------------------------------------

# Script bien formé pour freeform : delete default + 2 primitives + camera + light.
_GOOD_FREEFORM_SCRIPT = '''```python
import bpy

# Nettoyage scène par défaut
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete()

# Sphère
bpy.ops.mesh.primitive_uv_sphere_add(radius=1.0, location=(0, 0, 2))
sphere = bpy.context.object
sphere.name = "Sphere_Metal"

# Cube
bpy.ops.mesh.primitive_cube_add(size=2, location=(0, 0, 0))
cube = bpy.context.object
cube.name = "Cube_Red"

# Caméra
bpy.ops.object.camera_add(location=(5, -5, 4))
cam = bpy.context.object
bpy.context.scene.camera = cam

# Lumière
bpy.ops.object.light_add(type='AREA', location=(3, -3, 5))
key = bpy.context.object
key.name = "Key_Light"
```'''


# Script template interior_space conforme : 7 objets nommés.
_GOOD_INTERIOR_SCRIPT = '''```python
import bpy

bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete()

bpy.ops.mesh.primitive_plane_add(size=10, location=(0, 0, 0))
floor = bpy.context.object
floor.name = "Floor_Plane"

bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 5, 1.5))
wb = bpy.context.object
wb.name = "Wall_Back"

bpy.ops.mesh.primitive_cube_add(size=1, location=(-5, 0, 1.5))
wl = bpy.context.object
wl.name = "Wall_Left"

bpy.ops.mesh.primitive_cube_add(size=1, location=(5, 0, 1.5))
wr = bpy.context.object
wr.name = "Wall_Right"

bpy.ops.mesh.primitive_cylinder_add(radius=0.3, depth=1.8, location=(0, 0, 0.9))
ms = bpy.context.object
ms.name = "Main_Subject"

bpy.ops.object.camera_add(location=(0, -6, 2.2))
cam = bpy.context.object
cam.name = "Camera"
bpy.context.scene.camera = cam

bpy.ops.object.light_add(type='SUN', location=(4, -4, 6))
kl = bpy.context.object
kl.name = "Key_Light"
```'''


# Script template interior_space mais oublie 'Wall_Right'.
_INTERIOR_MISSING_WALL_RIGHT = _GOOD_INTERIOR_SCRIPT.replace(
    'wr.name = "Wall_Right"', 'wr.name = "Some_Other_Wall"'
)


# Script qui ne parse pas (SyntaxError).
_BROKEN_SCRIPT = '''```python
import bpy
this is not valid python at all !!!
```'''


# Script avec import asset externe interdit.
_SCRIPT_WITH_EXTERNAL_ASSET = '''```python
import bpy
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete()
bpy.ops.import_scene.obj(filepath="/path/to/model.obj")
bpy.ops.mesh.primitive_cube_add(size=2)
bpy.ops.object.camera_add(location=(7, -7, 5))
bpy.context.scene.camera = bpy.context.object
bpy.ops.object.light_add(type='SUN')
```'''


# Cas factices construits pour des assertions précises.
_FAKE_FREEFORM_CASE = next(
    c for c in DEFAULT_CASES if c.id == "freeform_metal_sphere_floating"
)
_FAKE_INTERIOR_CASE = next(
    c for c in DEFAULT_CASES if c.id == "interior_salon_moderne"
)


# ---------------------------------------------------------------------------
# score_script — branches élémentaires
# ---------------------------------------------------------------------------

def test_score_script_generation_ok_false_on_none_response() -> None:
    score = score_script(
        raw_response=None,
        case=_FAKE_FREEFORM_CASE,
        template_name_actual=None,
    )
    assert score.generation_ok is False
    assert score.python_extracted is False
    assert score.ast_parseable is False
    assert score.score == 0.0
    # generation_ok est dans per_check à False, les autres à None.
    assert score.per_check[CHECK_GENERATION_OK] is False
    assert score.per_check[CHECK_AST_PARSEABLE] is None


def test_score_script_generation_ok_false_on_empty_response() -> None:
    score = score_script(
        raw_response="   \n  ",
        case=_FAKE_FREEFORM_CASE,
        template_name_actual=None,
    )
    assert score.generation_ok is False
    assert score.score == 0.0


def test_score_script_ast_unparseable_forces_score_zero() -> None:
    score = score_script(
        raw_response=_BROKEN_SCRIPT,
        case=_FAKE_FREEFORM_CASE,
        template_name_actual=None,
    )
    assert score.generation_ok is True
    assert score.python_extracted is True
    assert score.ast_parseable is False
    # Décision D8 : ast_unparseable ⇒ score=0.0
    assert score.score == 0.0


def test_score_script_good_freeform_yields_high_score() -> None:
    score = score_script(
        raw_response=_GOOD_FREEFORM_SCRIPT,
        case=_FAKE_FREEFORM_CASE,
        template_name_actual=None,
    )
    assert score.generation_ok is True
    assert score.python_extracted is True
    assert score.ast_parseable is True
    assert score.score > 0.8, f"freeform good script score trop bas: {score.score}"


def test_score_script_external_asset_violation_drops_check() -> None:
    score = score_script(
        raw_response=_SCRIPT_WITH_EXTERNAL_ASSET,
        case=_FAKE_FREEFORM_CASE,
        template_name_actual=None,
    )
    # ast_parseable doit rester True (script syntaxiquement valide).
    assert score.ast_parseable is True
    # Le check no_external_assets DOIT être False.
    from app.engine.script_gen_eval_cases import CHECK_NO_EXTERNAL_ASSETS
    assert score.per_check[CHECK_NO_EXTERNAL_ASSETS] is False


def test_score_script_interior_conformant_all_objects_named() -> None:
    score = score_script(
        raw_response=_GOOD_INTERIOR_SCRIPT,
        case=_FAKE_INTERIOR_CASE,
        template_name_actual="interior_space",
    )
    assert score.ast_parseable is True
    assert score.template_required_objects_named is not None
    # Tous les 7 objets doivent être nommés.
    for obj_name, named in score.template_required_objects_named.items():
        assert named, f"Objet {obj_name} non détecté dans script conformant"
    assert score.per_check[CHECK_TEMPLATE_REQUIRED_OBJECTS] is True


def test_score_script_interior_missing_object_drops_check() -> None:
    score = score_script(
        raw_response=_INTERIOR_MISSING_WALL_RIGHT,
        case=_FAKE_INTERIOR_CASE,
        template_name_actual="interior_space",
    )
    assert score.template_required_objects_named is not None
    assert score.template_required_objects_named["Wall_Right"] is False
    # Les autres restent True
    for name, named in score.template_required_objects_named.items():
        if name != "Wall_Right":
            assert named, f"{name} devrait être détecté"
    assert score.per_check[CHECK_TEMPLATE_REQUIRED_OBJECTS] is False


def test_score_script_template_match_field() -> None:
    s = score_script(
        raw_response=_GOOD_FREEFORM_SCRIPT,
        case=_FAKE_INTERIOR_CASE,
        template_name_actual=None,  # divergence avec expected="interior_space"
    )
    assert s.template_match is False
    assert s.expected_template == "interior_space"
    assert s.selected_template is None


def test_score_script_duration_passed_through() -> None:
    s = score_script(
        raw_response=_GOOD_FREEFORM_SCRIPT,
        case=_FAKE_FREEFORM_CASE,
        template_name_actual=None,
        duration_seconds=2.5,
    )
    assert s.duration_seconds == 2.5


def test_score_script_error_passed_through() -> None:
    s = score_script(
        raw_response=None,
        case=_FAKE_FREEFORM_CASE,
        template_name_actual=None,
        error="ConnectionError: boom",
    )
    assert s.error == "ConnectionError: boom"
    assert s.score == 0.0


# ---------------------------------------------------------------------------
# resolve_template_for_message
# ---------------------------------------------------------------------------

def test_resolve_template_freeform_returns_none() -> None:
    _, scaffold, name = resolve_template_for_message(
        "Crée une sphère métallique flottant au-dessus d'un cube rouge mat"
    )
    assert scaffold is None
    assert name is None


def test_resolve_template_interior_salon_returns_interior_space() -> None:
    _, scaffold, name = resolve_template_for_message(
        "Crée une scène intérieure d'un salon moderne avec canapé"
    )
    assert scaffold is not None
    assert name == "interior_space"


def test_resolve_template_atelier_ambigu_returns_none() -> None:
    """Le cas ambigu doit bien retomber freeform (cf. cadrage §1)."""
    _, scaffold, name = resolve_template_for_message(
        "Atelier d'artiste avec un chevalet et des toiles posées contre le mur"
    )
    assert scaffold is None
    assert name is None


# ---------------------------------------------------------------------------
# run_harness avec generate_fn mocké
# ---------------------------------------------------------------------------

def test_run_harness_all_cases_with_constant_good_freeform_script() -> None:
    """
    Mocke generate_fn pour qu'il retourne toujours _GOOD_FREEFORM_SCRIPT.
    Les cas freeform passent ; les cas interior_space échouent partiellement
    (manque les objets template).
    """
    def fake_generate(model: str, prompt: str) -> str:
        return _GOOD_FREEFORM_SCRIPT

    report = run_harness(
        cases=DEFAULT_CASES,
        model="fake-model",
        generate_fn=fake_generate,
    )
    assert report.model == "fake-model"
    assert len(report.cases) == 5
    assert all(s.generation_ok for s in report.cases)
    assert all(s.ast_parseable for s in report.cases)

    # Les cas freeform doivent avoir un score élevé.
    freeform_scores = [
        s.score for s in report.cases
        if s.expected_template is None
    ]
    assert all(sc > 0.8 for sc in freeform_scores), freeform_scores

    # Les cas interior doivent avoir un score plus bas (objets manquants).
    interior_scores = [
        s.score for s in report.cases
        if s.expected_template == "interior_space"
    ]
    assert all(sc < 1.0 for sc in interior_scores), interior_scores


def test_run_harness_handles_generate_fn_exception_per_case() -> None:
    """Si generate_fn lève sur un cas, le harness continue et le score=0."""
    def flaky_generate(model: str, prompt: str) -> str:
        if "salon" in prompt:
            raise RuntimeError("ollama timeout")
        return _GOOD_FREEFORM_SCRIPT

    report = run_harness(
        cases=DEFAULT_CASES,
        model="fake-model",
        generate_fn=flaky_generate,
    )
    failed = [s for s in report.cases if s.error is not None]
    assert len(failed) == 1
    assert failed[0].case_id == "interior_salon_moderne"
    assert "ollama timeout" in failed[0].error
    assert failed[0].score == 0.0
    assert failed[0].generation_ok is False


def test_run_harness_aggregate_keys_present() -> None:
    def fake_generate(model: str, prompt: str) -> str:
        return _GOOD_FREEFORM_SCRIPT

    report = run_harness(
        cases=DEFAULT_CASES,
        model="fake-model",
        generate_fn=fake_generate,
    )
    agg = report.aggregate
    for key in (
        "n_cases", "mean_score", "score_stdev",
        "generation_ok_rate", "python_extracted_rate", "ast_parseable_rate",
        "per_check_pass_rate", "template_match_rate",
        "mean_duration_seconds", "total_duration_seconds",
    ):
        assert key in agg, f"clé manquante dans aggregate: {key}"
    assert agg["n_cases"] == 5
    assert 0.0 <= agg["mean_score"] <= 1.0


def test_run_harness_template_match_rate_when_freeform_script_returned() -> None:
    """Le LLM retourne tjs un script freeform → template_match basé sur
    le template effectivement résolu pour chaque message (pas sur la sortie LLM).
    """
    def fake_generate(model: str, prompt: str) -> str:
        return _GOOD_FREEFORM_SCRIPT

    report = run_harness(
        cases=DEFAULT_CASES,
        model="fake-model",
        generate_fn=fake_generate,
    )
    # template_match = (selected == expected).
    # On a 3 cas expected=None et 2 cas expected=interior_space.
    # selected_template est calculé par resolve_template_for_message
    # sur le PROMPT, pas sur le script généré.
    n_match = sum(1 for s in report.cases if s.template_match)
    # Sur les 5 cas du corpus, la résolution réelle doit matcher l'expected
    # (sinon le corpus est mal designé).
    assert n_match == 5, (
        f"template_match attendu 5/5, obtenu {n_match}/5. "
        f"Détail : "
        + ", ".join(
            f"{s.case_id}=(expected={s.expected_template}, "
            f"selected={s.selected_template})"
            for s in report.cases
        )
    )


# ---------------------------------------------------------------------------
# Sérialisation
# ---------------------------------------------------------------------------

def test_case_score_to_dict_is_json_safe() -> None:
    import json

    score = score_script(
        raw_response=_GOOD_INTERIOR_SCRIPT,
        case=_FAKE_INTERIOR_CASE,
        template_name_actual="interior_space",
        duration_seconds=1.5,
    )
    d = case_score_to_dict(score)
    # Roundtrip JSON doit fonctionner.
    s = json.dumps(d)
    d2 = json.loads(s)
    assert d2["case_id"] == "interior_salon_moderne"
    assert d2["template_required_objects_named"] is not None


def test_report_to_dict_structure() -> None:
    def fake_generate(model: str, prompt: str) -> str:
        return _GOOD_FREEFORM_SCRIPT

    report = run_harness(
        cases=DEFAULT_CASES[:2],  # subset pour la rapidité
        model="fake",
        generate_fn=fake_generate,
    )
    d = report_to_dict(report)
    assert d["model"] == "fake"
    assert isinstance(d["cases"], list)
    assert len(d["cases"]) == 2
    assert isinstance(d["aggregate"], dict)


def test_aggregate_empty_corpus_returns_zeros() -> None:
    """Edge case : corpus vide ne doit pas crasher."""
    def gen(_m, _p):  # pragma: no cover
        return "irrelevant"

    report = run_harness(cases=[], model="m", generate_fn=gen)
    assert report.aggregate["n_cases"] == 0
    assert report.aggregate["mean_score"] == 0.0
    assert report.aggregate["per_check_pass_rate"] == {}


# ---------------------------------------------------------------------------
# H.6.8.b.1 — extracted_code exposé dans ScriptGenCaseScore
# ---------------------------------------------------------------------------

def test_score_script_exposes_extracted_code_when_generation_ok() -> None:
    score = score_script(
        raw_response=_GOOD_FREEFORM_SCRIPT,
        case=_FAKE_FREEFORM_CASE,
        template_name_actual=None,
    )
    assert score.extracted_code is not None
    assert "import bpy" in score.extracted_code
    # On a bien dépouillé le markdown.
    assert "```" not in score.extracted_code


def test_score_script_extracted_code_none_when_generation_failed() -> None:
    score = score_script(
        raw_response=None,
        case=_FAKE_FREEFORM_CASE,
        template_name_actual=None,
    )
    assert score.extracted_code is None


def test_score_script_extracted_code_none_when_empty() -> None:
    score = score_script(
        raw_response="   ",
        case=_FAKE_FREEFORM_CASE,
        template_name_actual=None,
    )
    assert score.extracted_code is None


def test_case_score_to_dict_does_not_include_extracted_code() -> None:
    """
    `extracted_code` est volontairement absent du dict JSON pour ne pas
    bloater le rapport. Le runner persiste séparément via
    `persist_extracted_scripts`.
    """
    score = score_script(
        raw_response=_GOOD_FREEFORM_SCRIPT,
        case=_FAKE_FREEFORM_CASE,
        template_name_actual=None,
    )
    d = case_score_to_dict(score)
    assert "extracted_code" not in d


# ---------------------------------------------------------------------------
# H.6.8.b.2 — Stabilisation inférence (smoke)
# ---------------------------------------------------------------------------

def test_run_harness_uses_default_stabilised_fn_when_none_provided(monkeypatch) -> None:
    """
    Quand `generate_fn=None`, run_harness doit appeler
    `_default_generate_fn` (stabilisé). On vérifie indirectement en
    monkeypatchant `generate_with_ollama` et en confirmant que les
    options stabilisées sont transmises.
    """
    import app.engine.script_gen_eval_harness as harness_mod

    captured_options: list[dict] = []

    def fake_generate_with_ollama(model, prompt, **kwargs):
        captured_options.append(kwargs.get("options", {}))
        return "```python\nimport bpy\n```"

    monkeypatch.setattr(harness_mod, "generate_with_ollama", fake_generate_with_ollama)

    # Un seul cas pour limiter les appels
    report = run_harness(
        cases=DEFAULT_CASES[:1],
        model="any",
        generate_fn=None,  # → utilise _default_generate_fn
    )
    assert len(captured_options) == 1
    opts = captured_options[0]
    assert opts["temperature"] == 0.0
    assert opts["seed"] == 42
    assert opts["top_k"] == 1
    assert opts["num_ctx"] == 8192
