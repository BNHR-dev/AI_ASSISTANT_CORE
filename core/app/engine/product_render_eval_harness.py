"""
H.6.2 — Eval harness Product Render IR.

Mesure objective et reproductible de la qualité d'extraction d'un modèle LLM
sur la tâche `extract_product_render_intent`. Calcule un score par cas et
des métriques agrégées sur un corpus versionné.

But explicite :
- Permettre de comparer qwen2.5-coder:7b (défaut) à tout autre modèle via
  l'env `AAC_BLENDER_LLM_MODEL` (centralisé en H.6.1), **sans changer le
  modèle par défaut** et **sans toucher au runtime**.
- Servir de filet de mesure avant H.6.3 (format strict côté Ollama) et
  H.6.4 (QA visuelle).

Garanties :
- Pure : aucune dépendance Ollama dans le scoring ni dans `run_harness`
  quand un `generate_fn` est injecté. Testable hors-ligne.
- Déterministe : à entrée identique, sortie bit-équivalente. Pas de
  randomisation, pas de seuil flottant.
- Lecture seule sur l'extractor et l'IR : aucune mutation. Le builder
  Blender et le router/planner/executor ne sont pas touchés.

Format du scoring (par cas) :
- `parse_ok = (status == "parsed")`.
- Pour chaque clé présente dans `case.expected` (et seulement celles-là) :
  - les colors sont comparées après normalisation via `_validate_color_token`.
  - tout le reste : exact match.
- Si `parse_ok=False`, **score = 0.0** : la `FALLBACK_INTENT` ne doit pas
  pouvoir gonfler artificiellement le score sur les cas où elle se trouve
  proche de l'attendu. La distinction se voit dans `parse_ok_rate`.

Agrégat :
- `mean_score` : moyenne arithmétique des scores cas.
- `parse_ok_rate` : proportion de cas avec parse_ok=True.
- `per_field_accuracy` : par clé attendue, proportion de cas qui la
  satisfont (parmi les cas qui la spécifient).

Exécution en bench réel :
- Un bloc `__main__` lance le harness contre Ollama réel pour le modèle
  courant (`get_blender_llm_model()`). Best-effort, non testé, optionnel.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional

from app.engine.blender_model_config import get_blender_llm_model
from app.engine.product_render_eval_cases import (
    DEFAULT_CASES,
    EvalCase,
)
from app.engine.product_render_extractor import (
    ProductRenderExtractionResult,
    extract_product_render_intent,
)
from app.engine.product_render_ir import _validate_color_token


# ---------------------------------------------------------------------------
# Dataclasses de rapport
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CaseScore:
    """
    Résultat de scoring d'un cas unique.

    case_id        : id du cas évalué.
    parse_ok       : True si l'extracteur a renvoyé status="parsed".
    field_matches  : {clé attendue → bool}, seulement pour les clés que le
                     cas spécifie. Toujours ordonné comme `expected`.
    score          : matches/total ∈ [0.0, 1.0], ou 0.0 si parse_ok=False
                     OU si `expected` est vide (cas dégénéré).
    actual         : sortie aplatie de l'IR (mêmes clés que `expected`),
                     pour diagnostic. Vide si parse_ok=False.
    error          : reproduit `extraction_result.error` (None si parsed).
    """

    case_id: str
    parse_ok: bool
    field_matches: Mapping[str, bool]
    score: float
    actual: Mapping[str, Any]
    error: Optional[str]


@dataclass(frozen=True)
class HarnessReport:
    """
    Rapport agrégé sur un run du harness.

    model              : modèle LLM utilisé (informatif).
    case_scores        : tuple de CaseScore, dans l'ordre du corpus.
    total_cases        : len(case_scores).
    parse_ok_rate      : proportion de cas avec parse_ok=True (0..1).
    mean_score         : moyenne arithmétique des scores cas (0..1).
    per_field_accuracy : par clé attendue, proportion de cas qui la
                         satisfont (parmi ceux qui la spécifient).
    """

    model: str
    case_scores: tuple[CaseScore, ...]
    total_cases: int
    parse_ok_rate: float
    mean_score: float
    per_field_accuracy: Mapping[str, float]


# ---------------------------------------------------------------------------
# Flatten IR → dict aligné sur `expected`
# ---------------------------------------------------------------------------

def _flatten_intent(result: ProductRenderExtractionResult) -> dict[str, Any]:
    """
    Aplatit `result.intent` en dict aux mêmes clés que `EvalCase.expected`.

    Retourne TOUTES les clés présentes dans `ALLOWED_EXPECTED_KEYS` que
    l'IR fournit effectivement (champs None V1 → absents). Permet ensuite
    une comparaison clé-à-clé propre.
    """
    if result.intent is None:
        return {}
    intent = result.intent
    flat: dict[str, Any] = {
        "schema_version": intent.schema_version,
        "subject.kind": intent.subject.kind,
        "subject.color": intent.subject.color,
        "subject.material": intent.subject.material,
        "backdrop.color": intent.backdrop.color,
    }
    # Champs V1 : présents seulement s'ils sont non-None côté IR.
    if intent.subject.shape is not None:
        flat["subject.shape"] = intent.subject.shape
    if intent.subject.cap is not None:
        flat["subject.cap"] = intent.subject.cap
    if intent.subject.transparency is not None:
        flat["subject.transparency"] = intent.subject.transparency
    if intent.framing is not None:
        flat["framing"] = intent.framing
    # Champs semantic_fidelity_v1 : mêmes règles (non-None → présent).
    if intent.subject.kind_fidelity is not None:
        flat["subject.kind_fidelity"] = intent.subject.kind_fidelity
    if intent.pedestal is not None:
        flat["pedestal.color"] = intent.pedestal.color
        flat["pedestal.material"] = intent.pedestal.material
    return flat


# ---------------------------------------------------------------------------
# Comparaison atomique d'un champ
# ---------------------------------------------------------------------------

# Clés dont la valeur doit être normalisée comme un token couleur avant
# comparaison (lowercase, hex normalisé). Source de vérité : product_render_ir.
_COLOR_FIELDS: frozenset[str] = frozenset(
    {"subject.color", "backdrop.color", "pedestal.color"}
)


def _values_match(key: str, expected: Any, actual: Any) -> bool:
    """
    Compare deux valeurs pour une clé donnée. Renvoie False si `actual` est
    absent (None). Pour les couleurs, normalise les deux côtés.
    """
    if actual is None:
        return False
    if key in _COLOR_FIELDS:
        try:
            return _validate_color_token(str(expected)) == _validate_color_token(str(actual))
        except ValueError:
            return False
    return expected == actual


# ---------------------------------------------------------------------------
# Scoring d'un cas
# ---------------------------------------------------------------------------

def score_case(
    case: EvalCase,
    result: ProductRenderExtractionResult,
) -> CaseScore:
    """
    Score un cas. Déterministe, pure.

    - `parse_ok=False` ⇒ score=0.0 et `field_matches[k]=False` pour toutes
      les clés attendues. La FALLBACK_INTENT ne doit pas pouvoir gonfler
      un score en correspondant fortuitement à l'attendu.
    - `expected` vide ⇒ score=0.0 (cas dégénéré ; aucun cas du corpus
      n'est censé être vide, mais on évite la division par zéro).
    """
    parse_ok = result.status == "parsed"
    actual = _flatten_intent(result) if parse_ok else {}

    field_matches: dict[str, bool] = {}
    for key, exp_val in case.expected.items():
        if not parse_ok:
            field_matches[key] = False
            continue
        field_matches[key] = _values_match(key, exp_val, actual.get(key))

    total = len(case.expected)
    if total == 0 or not parse_ok:
        score = 0.0
    else:
        matches = sum(1 for ok in field_matches.values() if ok)
        score = matches / total

    return CaseScore(
        case_id=case.id,
        parse_ok=parse_ok,
        field_matches=field_matches,
        score=score,
        actual=actual,
        error=result.error,
    )


# ---------------------------------------------------------------------------
# Agrégation
# ---------------------------------------------------------------------------

def _aggregate(
    model: str,
    cases: tuple[EvalCase, ...],
    scores: tuple[CaseScore, ...],
) -> HarnessReport:
    total = len(scores)
    if total == 0:
        return HarnessReport(
            model=model,
            case_scores=(),
            total_cases=0,
            parse_ok_rate=0.0,
            mean_score=0.0,
            per_field_accuracy={},
        )

    parse_ok_count = sum(1 for s in scores if s.parse_ok)
    parse_ok_rate = parse_ok_count / total

    mean_score = sum(s.score for s in scores) / total

    # per_field_accuracy : pour chaque clé, on regarde tous les cas qui la
    # spécifient (dénominateur = nombre de cas avec cette clé dans expected).
    field_totals: dict[str, int] = {}
    field_hits: dict[str, int] = {}
    for case, score in zip(cases, scores):
        for key in case.expected:
            field_totals[key] = field_totals.get(key, 0) + 1
            if score.field_matches.get(key, False):
                field_hits[key] = field_hits.get(key, 0) + 1
    per_field_accuracy = {
        key: field_hits.get(key, 0) / field_totals[key]
        for key in sorted(field_totals)
    }

    return HarnessReport(
        model=model,
        case_scores=scores,
        total_cases=total,
        parse_ok_rate=parse_ok_rate,
        mean_score=mean_score,
        per_field_accuracy=per_field_accuracy,
    )


# ---------------------------------------------------------------------------
# Orchestrateur — run_harness
# ---------------------------------------------------------------------------

def run_harness(
    *,
    generate_fn: Optional[Callable[[str, str], str]] = None,
    model: Optional[str] = None,
    cases: Optional[tuple[EvalCase, ...]] = None,
) -> HarnessReport:
    """
    Exécute le harness sur un corpus.

    - `generate_fn` : si fourni, l'extracteur l'utilise. Permet d'exécuter
      le harness en test unitaire sans Ollama. Si None, l'extracteur tape
      sur Ollama via `generate_with_ollama` (=> ne PAS appeler en CI sans
      Ollama disponible).
    - `model`       : nom du modèle. Si None, lit la config centralisée.
    - `cases`       : corpus à utiliser. Par défaut `DEFAULT_CASES`.

    Pure quand `generate_fn` est fourni.
    """
    if cases is None:
        cases = DEFAULT_CASES
    if model is None:
        model = get_blender_llm_model()

    scores: list[CaseScore] = []
    for case in cases:
        result = extract_product_render_intent(
            case.prompt,
            model=model,
            generate_fn=generate_fn,
        )
        scores.append(score_case(case, result))

    return _aggregate(model, cases, tuple(scores))


# ---------------------------------------------------------------------------
# Sérialisation rapport (utile pour bench manuel)
# ---------------------------------------------------------------------------

def report_to_dict(report: HarnessReport) -> dict[str, Any]:
    """Représentation JSON-sérialisable d'un rapport, utile pour artefacts."""
    return {
        "model": report.model,
        "total_cases": report.total_cases,
        "parse_ok_rate": report.parse_ok_rate,
        "mean_score": report.mean_score,
        "per_field_accuracy": dict(report.per_field_accuracy),
        "case_scores": [
            {
                "case_id": s.case_id,
                "parse_ok": s.parse_ok,
                "score": s.score,
                "field_matches": dict(s.field_matches),
                "actual": dict(s.actual),
                "error": s.error,
            }
            for s in report.case_scores
        ],
    }


# ---------------------------------------------------------------------------
# Bench réel — entrée __main__ (best-effort, non testée)
# ---------------------------------------------------------------------------
# Exécution manuelle :
#   python -m app.engine.product_render_eval_harness
#   python -m app.engine.product_render_eval_harness --model qwen2.5:14b
#
# N'est pas appelée par la suite de tests : aucune dépendance Ollama dans
# les chemins testés.

def _main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Bench réel Ollama du harness Product Render IR.",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Nom du modèle Ollama (défaut : AAC_BLENDER_LLM_MODEL).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Émettre le rapport en JSON sur stdout.",
    )
    args = parser.parse_args(argv)

    report = run_harness(model=args.model)

    if args.json:
        json.dump(report_to_dict(report), sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        print(f"model              : {report.model}")
        print(f"total_cases        : {report.total_cases}")
        print(f"parse_ok_rate      : {report.parse_ok_rate:.3f}")
        print(f"mean_score         : {report.mean_score:.3f}")
        print("per_field_accuracy :")
        for k, v in report.per_field_accuracy.items():
            print(f"  {k:<28} {v:.3f}")
        print("case_scores :")
        for s in report.case_scores:
            flag = "OK " if s.parse_ok else "FB "
            print(f"  {flag} {s.case_id:<48} {s.score:.3f}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
