"""
H.6.8.a — Eval harness `script_gen`.

Mesure objective et reproductible de la qualité du `scene.py` produit par
le LLM via `build_script_gen_prompt` (extrait en H.6.8.a). Calcule un
score par cas et des métriques agrégées sur un corpus versionné.

But explicite :
- Construire une baseline mesurée du SECOND site LLM de la pipeline
  Blender (le premier — extractor IR — a été mesuré en H.6.x).
- Sans changer le modèle par défaut. Sans exécuter Blender. Sans rendre.
- Sans appliquer le moindre seuil pass/fail (rapport descriptif).

Garanties :
- Pure : aucune dépendance Ollama dans le scoring ni dans `run_harness`
  quand un `generate_fn` est injecté. Testable hors-ligne.
- Déterministe sur le scoring : à `raw_response` identique, sortie
  bit-équivalente. Pas de randomisation, pas de seuil flottant.
- Lecture seule sur AST guard, templates, blender_client : aucune
  mutation. Le builder Blender et le router/planner/executor ne sont
  pas touchés.

Format du scoring (par cas) :
- Pour chaque check listé dans `case.expected["applicable_checks"]` :
  bool (passé / non passé). Les autres checks ne sont pas comptés.
- `score = checks_passés / checks_applicables` ∈ [0.0, 1.0].
- **Exception** : si `ast_parseable=False`, `score=0.0` automatique
  (par analogie stricte avec `parse_ok=False ⇒ score=0.0` de
  l'extractor harness H.6.2).
- `generation_ok=False` (réponse LLM vide) : `score=0.0` et tous les
  autres checks sont `None` (non évaluables).

Agrégat :
- `mean_score`             : moyenne arithmétique des scores cas.
- `generation_ok_rate`     : proportion de cas où generation_ok=True.
- `python_extracted_rate`  : idem extraction markdown.
- `ast_parseable_rate`     : idem AST parseable.
- `per_check_pass_rate`    : par check, proportion de cas applicables
                             qui le satisfont (cf. extractor `per_field_accuracy`).
- `template_match_rate`    : proportion de cas où le template
                             effectivement sélectionné == expected.

Exécution en bench réel :
- Le runner `script_gen_eval_runner` gère la persistance JSON. Le
  harness lui-même expose un `__main__` minimal pour debug rapide.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional, Sequence

from app.clients.blender_client import (
    _extract_python_from_markdown,
    build_script_gen_prompt,
)
from app.clients.ollama_client import generate_with_ollama
from app.engine.artistic_intent import parse_artistic_intent
from app.engine.blender_ast_guard import (
    V_AST_UNPARSEABLE,
    V_TEMPLATE_FORBIDDEN_PREFIX,
    V_TEMPLATE_REQUIRED_PREFIX,
    analyze_scene_py,
)
from app.engine.blender_model_config import get_blender_llm_model
from app.engine.blender_templates import (
    TEMPLATE_SPECS,
    get_template_name,
    get_template_name_from_intent,
    select_template,
    select_template_from_intent,
)
from app.engine.script_gen_eval_cases import (
    ALL_CHECKS,
    CHECK_ACTIVE_CAMERA_ASSIGNED,
    CHECK_AST_PARSEABLE,
    CHECK_DELETE_DEFAULT_PRESENT,
    CHECK_GENERATION_OK,
    CHECK_HAS_PRIMITIVE_GEOMETRY,
    CHECK_MESHES_NEW_HAS_GEOMETRY,
    CHECK_NOT_FALLBACK_CUBE_SUN_ONLY,
    CHECK_NO_EXTERNAL_ASSETS,
    CHECK_NO_PLACEHOLDER_PATHS,
    CHECK_PYTHON_EXTRACTED,
    CHECK_SCRIPT_MIN_SIZE,
    CHECK_TEMPLATE_FORBIDDEN_PREFIX,
    CHECK_TEMPLATE_REQUIRED_OBJECTS,
    DEFAULT_CASES,
    ScriptGenCase,
)


# ---------------------------------------------------------------------------
# Correspondance check canonique → check AST guard
# ---------------------------------------------------------------------------
# Les checks "non-AST" (CHECK_GENERATION_OK, CHECK_PYTHON_EXTRACTED) ne
# sont PAS dans ce mapping ; ils sont évalués hors AST guard.
# Les checks "template_*" sont composés : ils dépendent du contenu
# `template_required_objects.violations` filtré par préfixe.

_CHECK_TO_AST_GUARD_KEY: dict[str, str] = {
    CHECK_AST_PARSEABLE:              "ast_parseable",
    CHECK_NO_EXTERNAL_ASSETS:         "no_external_assets",
    CHECK_NO_PLACEHOLDER_PATHS:       "no_placeholder_paths",
    CHECK_HAS_PRIMITIVE_GEOMETRY:     "has_primitive_geometry",
    CHECK_MESHES_NEW_HAS_GEOMETRY:    "meshes_new_has_from_pydata",
    CHECK_SCRIPT_MIN_SIZE:            "script_min_size",
    CHECK_ACTIVE_CAMERA_ASSIGNED:     "active_camera_assigned",
    CHECK_NOT_FALLBACK_CUBE_SUN_ONLY: "fallback_cube_sun_only",
    CHECK_DELETE_DEFAULT_PRESENT:     "delete_default_present",
}


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ScriptGenCaseScore:
    """
    Résultat de scoring d'un cas unique.

    case_id         : id du cas évalué.
    expected_template : tel que déclaré par le cas.
    selected_template : tel que résolu par les helpers (peut différer).
    template_match    : selected_template == expected_template.

    generation_ok          : LLM a retourné une str non vide.
    python_extracted       : extraction markdown a produit du code non vide.
    python_extracted_len_chars : longueur du code extrait (chars).
    python_extracted_len_lines : longueur du code extrait (lignes).

    ast_parseable          : alias direct du check homonyme (présent ici pour
                             lisibilité agrégée).
    ast_guard_violations   : liste brute des violations AST guard.
    ast_guard_violations_count : taille de ast_guard_violations.

    per_check              : dict[check_name -> bool | None]. None si le check
                             est non applicable ou non évaluable (ex:
                             generation_ok=False).
    applicable_checks      : repris du cas pour traçabilité.

    template_required_objects_named : dict[obj_name -> bool] ou None si pas
                             applicable. Détail par objet requis.

    duration_seconds       : durée d'inférence LLM mesurée.
    score                  : checks passés / checks applicables ∈ [0,1].
                             0.0 si ast_parseable=False ou generation_ok=False.
    error                  : message d'erreur du generate_fn si exception, None
                             sinon.
    """
    case_id: str
    expected_template: str | None
    selected_template: str | None
    template_match: bool

    generation_ok: bool
    python_extracted: bool
    python_extracted_len_chars: int
    python_extracted_len_lines: int

    ast_parseable: bool
    ast_guard_violations: tuple[str, ...]
    ast_guard_violations_count: int

    per_check: Mapping[str, bool | None]
    applicable_checks: tuple[str, ...]

    template_required_objects_named: Mapping[str, bool] | None

    duration_seconds: float
    score: float
    error: str | None


@dataclass(frozen=True)
class ScriptGenHarnessReport:
    """
    Rapport agrégé d'une passe harness sur un corpus.

    model           : nom du modèle LLM utilisé.
    cases           : scores par cas, dans l'ordre du corpus.
    aggregate       : dict des métriques agrégées (cf. report_to_dict).
    """
    model: str
    cases: tuple[ScriptGenCaseScore, ...]
    aggregate: Mapping[str, Any]


# ---------------------------------------------------------------------------
# Résolution template (réplique de la logique de build_blender_script)
# ---------------------------------------------------------------------------
# Ce helper duplique 5 lignes de logique de `build_blender_script`. Le
# refactor d'extraction H.6.8.a a porté UNIQUEMENT sur l'assemblage du
# prompt (`build_script_gen_prompt`), pas sur la résolution template.
# Cette duplication est minimale et documentée ; toute évolution future
# de la résolution template devra mettre à jour les DEUX endroits ou
# faire un second refactor.

def resolve_template_for_message(message: str) -> tuple[object, str | None, str | None]:
    """
    Résout (intent, template_scaffold, template_name) pour un prompt
    utilisateur en répliquant exactement la séquence de
    `build_blender_script`.

    Retourne :
        (intent, template_scaffold, template_name)

    intent peut être un ArtisticIntent ou None.
    template_scaffold est le contenu du scaffold ou None.
    template_name est "interior_space" | "product_render" | None.
    """
    intent = parse_artistic_intent(message)
    template_scaffold = select_template_from_intent(intent)
    template_name = get_template_name_from_intent(intent)
    if template_scaffold is None:
        template_scaffold = select_template(message)
        template_name = get_template_name(message)
    return intent, template_scaffold, template_name


# ---------------------------------------------------------------------------
# Scoring d'un script unique
# ---------------------------------------------------------------------------

def _split_template_violations(
    template_check_violations: Sequence[str],
) -> tuple[list[str], list[str]]:
    """
    Sépare les violations du check `template_required_objects` en
    (manquants, préfixes interdits trouvés).
    """
    missing: list[str] = []
    forbidden: list[str] = []
    for v in template_check_violations:
        if v.startswith(V_TEMPLATE_REQUIRED_PREFIX):
            missing.append(v[len(V_TEMPLATE_REQUIRED_PREFIX):])
        elif v.startswith(V_TEMPLATE_FORBIDDEN_PREFIX):
            forbidden.append(v[len(V_TEMPLATE_FORBIDDEN_PREFIX):])
    return missing, forbidden


def _ast_check_passed(ast_report: Mapping[str, Any], ast_key: str) -> bool:
    """Renvoie True si le check AST nommé est `passed`."""
    checks = ast_report.get("checks") or {}
    check = checks.get(ast_key)
    if not isinstance(check, Mapping):
        return False
    return check.get("status") == "passed"


def score_script(
    raw_response: str | None,
    case: ScriptGenCase,
    *,
    template_name_actual: str | None,
    error: str | None = None,
    duration_seconds: float = 0.0,
) -> ScriptGenCaseScore:
    """
    Score un script LLM brut contre un cas.

    Args:
        raw_response: réponse brute du LLM (ou None si exception).
        case: cas d'évaluation.
        template_name_actual: template tel que résolu pour ce message
            (pour comparaison avec case.expected.template).
        error: message d'erreur si generate_fn a levé, None sinon.
        duration_seconds: durée d'inférence mesurée.

    Returns:
        ScriptGenCaseScore complet.
    """
    expected_template = case.expected.get("template")
    template_match = template_name_actual == expected_template
    applicable: tuple[str, ...] = tuple(
        case.expected.get("applicable_checks", ())
    )
    expected_required: list[str] = list(
        case.expected.get("must_name_objects", [])
    )

    # 1. generation_ok
    generation_ok = bool(raw_response) and bool(raw_response.strip())

    # 2. python_extracted
    if generation_ok:
        extracted = _extract_python_from_markdown(raw_response)
    else:
        extracted = ""
    python_extracted = bool(extracted) and bool(extracted.strip())

    # 3. AST guard (uniquement si on a du code extrait)
    if python_extracted:
        ast_report = analyze_scene_py(extracted, template_name_actual)
    else:
        ast_report = {
            "status": "skipped",
            "violations": [],
            "checks": {},
            "metrics": {},
        }

    ast_parseable = (
        python_extracted
        and V_AST_UNPARSEABLE not in (ast_report.get("violations") or [])
    )

    ast_guard_violations: tuple[str, ...] = tuple(ast_report.get("violations") or [])

    # 4. per_check : un bool | None par check ALL_CHECKS
    per_check: dict[str, bool | None] = {check: None for check in ALL_CHECKS}

    # Si génération OK, on évalue tous les checks. Sinon ils restent None.
    if generation_ok:
        per_check[CHECK_GENERATION_OK] = True
        per_check[CHECK_PYTHON_EXTRACTED] = python_extracted

        if python_extracted:
            for canonical_check, ast_key in _CHECK_TO_AST_GUARD_KEY.items():
                per_check[canonical_check] = _ast_check_passed(ast_report, ast_key)

            # Checks template composés à partir de `template_required_objects`.
            template_check = (ast_report.get("checks") or {}).get(
                "template_required_objects", {}
            )
            template_violations = template_check.get("violations", []) if isinstance(
                template_check, Mapping
            ) else []
            missing, forbidden = _split_template_violations(template_violations)
            # CHECK_TEMPLATE_REQUIRED_OBJECTS : pour le cas, on ne regarde QUE
            # les noms réellement listés dans expected.must_name_objects.
            # Cela évite de pénaliser un cas si une violation porte sur un
            # objet hors expected (improbable avec TEMPLATE_SPECS mais
            # sémantiquement correct).
            if expected_required:
                missing_within_expected = [
                    name for name in missing if name in expected_required
                ]
                per_check[CHECK_TEMPLATE_REQUIRED_OBJECTS] = (
                    len(missing_within_expected) == 0
                )
            per_check[CHECK_TEMPLATE_FORBIDDEN_PREFIX] = (len(forbidden) == 0)
        else:
            # Python pas extrait : tous les checks AST restent None
            pass
    else:
        per_check[CHECK_GENERATION_OK] = False

    # 5. Détail template_required_objects_named (uniquement si applicable)
    template_required_objects_named: dict[str, bool] | None
    if (
        CHECK_TEMPLATE_REQUIRED_OBJECTS in applicable
        and expected_required
        and python_extracted
    ):
        template_check = (ast_report.get("checks") or {}).get(
            "template_required_objects", {}
        )
        template_violations = template_check.get("violations", []) if isinstance(
            template_check, Mapping
        ) else []
        missing, _ = _split_template_violations(template_violations)
        missing_set = set(missing)
        template_required_objects_named = {
            obj_name: (obj_name not in missing_set)
            for obj_name in expected_required
        }
    else:
        template_required_objects_named = None

    # 6. Score : passes parmi applicables, avec exception ast_unparseable.
    if not generation_ok:
        score = 0.0
    elif python_extracted and not ast_parseable:
        # Cf. décision D8 du cadrage : ast_unparseable ⇒ score=0.0.
        score = 0.0
    else:
        applicable_results: list[bool] = []
        for check in applicable:
            value = per_check.get(check)
            if value is None:
                # Check applicable mais non évaluable → faux (ex: python
                # non extrait sur un cas qui exigeait ast_parseable).
                applicable_results.append(False)
            else:
                applicable_results.append(value)
        if applicable_results:
            score = sum(1 for v in applicable_results if v) / len(applicable_results)
        else:
            score = 0.0

    # 7. Longueurs python extrait
    extracted_len_chars = len(extracted) if extracted else 0
    extracted_len_lines = extracted.count("\n") + 1 if extracted else 0

    return ScriptGenCaseScore(
        case_id=case.id,
        expected_template=expected_template,
        selected_template=template_name_actual,
        template_match=template_match,
        generation_ok=generation_ok,
        python_extracted=python_extracted,
        python_extracted_len_chars=extracted_len_chars,
        python_extracted_len_lines=extracted_len_lines,
        ast_parseable=ast_parseable,
        ast_guard_violations=ast_guard_violations,
        ast_guard_violations_count=len(ast_guard_violations),
        per_check=per_check,
        applicable_checks=applicable,
        template_required_objects_named=template_required_objects_named,
        duration_seconds=duration_seconds,
        score=score,
        error=error,
    )


# ---------------------------------------------------------------------------
# Run harness
# ---------------------------------------------------------------------------

# Signature d'une fonction d'inférence injectable.
# Conforme à `generate_with_ollama(model, prompt) -> str`.
GenerateFn = Callable[[str, str], str]


def _default_generate_fn(model: str, prompt: str) -> str:
    """Inférence Ollama réelle par défaut, utilisée hors tests."""
    return generate_with_ollama(model, prompt)


def run_harness(
    cases: Sequence[ScriptGenCase] = DEFAULT_CASES,
    model: str | None = None,
    *,
    generate_fn: Optional[GenerateFn] = None,
) -> ScriptGenHarnessReport:
    """
    Exécute le harness sur un corpus.

    Args:
        cases: corpus à évaluer (défaut: DEFAULT_CASES).
        model: nom du modèle LLM (défaut: get_blender_llm_model()).
        generate_fn: fonction d'inférence injectable (défaut: Ollama réel).

    Returns:
        ScriptGenHarnessReport.
    """
    chosen_model = model if model is not None else get_blender_llm_model()
    gen = generate_fn if generate_fn is not None else _default_generate_fn

    case_scores: list[ScriptGenCaseScore] = []

    for case in cases:
        intent, template_scaffold, template_name_actual = resolve_template_for_message(
            case.prompt
        )
        prompt = build_script_gen_prompt(
            message=case.prompt,
            intent=intent,
            template_scaffold=template_scaffold,
            template_name=template_name_actual,
        )

        raw_response: str | None = None
        error: str | None = None
        t0 = time.perf_counter()
        try:
            raw_response = gen(chosen_model, prompt)
        except Exception as exc:  # noqa: BLE001
            error = f"{type(exc).__name__}: {exc}"
        duration = time.perf_counter() - t0

        score = score_script(
            raw_response=raw_response,
            case=case,
            template_name_actual=template_name_actual,
            error=error,
            duration_seconds=duration,
        )
        case_scores.append(score)

    aggregate = _aggregate(case_scores)
    return ScriptGenHarnessReport(
        model=chosen_model,
        cases=tuple(case_scores),
        aggregate=aggregate,
    )


# ---------------------------------------------------------------------------
# Agrégation
# ---------------------------------------------------------------------------

def _aggregate(scores: Sequence[ScriptGenCaseScore]) -> dict[str, Any]:
    """Calcule les métriques agrégées sur un corpus de scores."""
    n = len(scores)
    if n == 0:
        return {
            "n_cases": 0,
            "mean_score": 0.0,
            "score_stdev": 0.0,
            "generation_ok_rate": 0.0,
            "python_extracted_rate": 0.0,
            "ast_parseable_rate": 0.0,
            "per_check_pass_rate": {},
            "template_match_rate": 0.0,
            "mean_duration_seconds": 0.0,
            "total_duration_seconds": 0.0,
        }

    scores_values = [s.score for s in scores]
    mean_score = sum(scores_values) / n
    # stdev sans dépendance numpy : variance population.
    variance = sum((v - mean_score) ** 2 for v in scores_values) / n
    stdev = variance ** 0.5

    # Per-check rate : pour chaque check, on regarde les cas où il est
    # applicable (i.e. dans case.applicable_checks ET per_check != None).
    per_check_counts: dict[str, list[bool]] = {}
    for s in scores:
        for check in s.applicable_checks:
            value = s.per_check.get(check)
            if value is None:
                # Applicable mais non évaluable : compte comme False
                # (cf. score_script — un check applicable doit être
                # évaluable, sinon c'est un signal négatif).
                per_check_counts.setdefault(check, []).append(False)
            else:
                per_check_counts.setdefault(check, []).append(value)

    per_check_pass_rate: dict[str, float] = {
        check: (sum(1 for v in values if v) / len(values))
        for check, values in per_check_counts.items()
        if values
    }

    total_duration = sum(s.duration_seconds for s in scores)

    return {
        "n_cases": n,
        "mean_score": mean_score,
        "score_stdev": stdev,
        "generation_ok_rate": sum(1 for s in scores if s.generation_ok) / n,
        "python_extracted_rate": sum(1 for s in scores if s.python_extracted) / n,
        "ast_parseable_rate": sum(1 for s in scores if s.ast_parseable) / n,
        "per_check_pass_rate": per_check_pass_rate,
        "template_match_rate": sum(1 for s in scores if s.template_match) / n,
        "mean_duration_seconds": total_duration / n,
        "total_duration_seconds": total_duration,
    }


# ---------------------------------------------------------------------------
# Sérialisation
# ---------------------------------------------------------------------------

def case_score_to_dict(score: ScriptGenCaseScore) -> dict[str, Any]:
    """Sérialise un CaseScore pour rapport JSON."""
    return {
        "case_id": score.case_id,
        "expected_template": score.expected_template,
        "selected_template": score.selected_template,
        "template_match": score.template_match,
        "generation_ok": score.generation_ok,
        "python_extracted": score.python_extracted,
        "python_extracted_len_chars": score.python_extracted_len_chars,
        "python_extracted_len_lines": score.python_extracted_len_lines,
        "ast_parseable": score.ast_parseable,
        "ast_guard_violations": list(score.ast_guard_violations),
        "ast_guard_violations_count": score.ast_guard_violations_count,
        "per_check": dict(score.per_check),
        "applicable_checks": list(score.applicable_checks),
        "template_required_objects_named": (
            dict(score.template_required_objects_named)
            if score.template_required_objects_named is not None
            else None
        ),
        "duration_seconds": score.duration_seconds,
        "score": score.score,
        "error": score.error,
    }


def report_to_dict(report: ScriptGenHarnessReport) -> dict[str, Any]:
    """Sérialise un HarnessReport pour rapport JSON (sans header schema/timestamp)."""
    return {
        "model": report.model,
        "cases": [case_score_to_dict(s) for s in report.cases],
        "aggregate": dict(report.aggregate),
    }
