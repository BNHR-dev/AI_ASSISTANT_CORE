"""
H.6.3 — Runner d'eval persistant pour Product Render IR.

Encapsule l'exécution du harness H.6.2 et la **sauvegarde** d'un rapport
JSON timestampé sous `outputs/blender/_eval_reports/`. Permet d'établir et
de comparer des baselines reproductibles entre modèles LLM.

Séparation des responsabilités vis-à-vis de H.6.2 :
- `product_render_eval_harness` : mesure (corpus, scoring, agrégation).
- `product_render_eval_runner`  : exécution datée + persistance.

Garanties :
- Pure quand `generate_fn` est fourni → testable hors-ligne.
- Aucune mutation runtime : ne touche ni router/planner/executor, ni
  builder, ni IR, ni modèle par défaut.
- Conventions de nommage fichier stables (pas de timestamp local-time, pas
  de séparateurs ambigus) pour rendre le tri lexicographique = tri
  chronologique.

Exécution réelle (manuelle, hors CI) :
    python -m app.engine.product_render_eval_runner
    python -m app.engine.product_render_eval_runner --model qwen2.5:14b
    python -m app.engine.product_render_eval_runner --base-dir my_reports
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

from app.engine.blender_model_config import get_blender_llm_model
from app.engine.product_render_eval_cases import DEFAULT_CASES, EvalCase
from app.engine.product_render_eval_harness import (
    HarnessReport,
    report_to_dict,
    run_harness,
)
from app.engine.product_render_extractor import build_extraction_generate_fn


# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

DEFAULT_EVAL_REPORTS_DIR = Path("outputs/blender/_eval_reports")

# Slug fichier : autorise lettres/chiffres/`.`/`-`. Tout le reste devient `-`.
# Volontairement conservateur (pas d'`_` introduit) pour rester compatible
# avec divers systèmes de fichiers et lectures CLI.
_SLUG_BAD_CHARS = re.compile(r"[^A-Za-z0-9.\-]+")


# ---------------------------------------------------------------------------
# Helpers chemin / nommage
# ---------------------------------------------------------------------------

def slugify_model(model: str) -> str:
    """
    Transforme un nom de modèle Ollama en slug filesystem-safe.

    Exemples :
      "qwen2.5-coder:7b" → "qwen2.5-coder-7b"
      "Qwen/Qwen2.5-VL:3B" → "Qwen-Qwen2.5-VL-3B"
      ""                   → "unknown-model"
    """
    if not model or not model.strip():
        return "unknown-model"
    slug = _SLUG_BAD_CHARS.sub("-", model.strip())
    slug = slug.strip("-")
    return slug or "unknown-model"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _format_timestamp(now: datetime) -> str:
    """
    Format `YYYY-MM-DDTHHMMSSZ` : ISO-8601 sans `:` (filesystem-safe sous
    Windows) ni microsecondes (lisibilité). Tri lexico = tri chronologique.
    """
    return now.strftime("%Y-%m-%dT%H%M%SZ")


def build_report_path(
    *,
    model: str,
    now: datetime,
    base_dir: Path = DEFAULT_EVAL_REPORTS_DIR,
) -> Path:
    """Construit le chemin canonique d'un rapport pour (model, now)."""
    fname = f"{_format_timestamp(now)}_{slugify_model(model)}.json"
    return base_dir / fname


# ---------------------------------------------------------------------------
# Sérialisation enrichie
# ---------------------------------------------------------------------------

def _enrich_report(report: HarnessReport, *, now: datetime) -> dict[str, Any]:
    """
    Ajoute les métadonnées de runner (timestamp, schema_version du rapport)
    au-dessus du `report_to_dict` du harness. Préserve la rétro-compatibilité :
    tout consommateur de `report_to_dict` continue de fonctionner.
    """
    payload = report_to_dict(report)
    enriched: dict[str, Any] = {
        "report_schema_version": "1",
        "timestamp": now.isoformat(),
    }
    enriched.update(payload)
    return enriched


# ---------------------------------------------------------------------------
# save_report — persiste un HarnessReport
# ---------------------------------------------------------------------------

def save_report(
    report: HarnessReport,
    *,
    base_dir: Optional[Path] = None,
    now: Optional[datetime] = None,
) -> Path:
    """
    Persiste un rapport JSON timestampé et retourne le chemin écrit.

    base_dir : répertoire de destination. Défaut `DEFAULT_EVAL_REPORTS_DIR`.
    now      : timestamp injecté pour reproductibilité tests. Défaut UTC now.

    Crée `base_dir` au besoin. Écrit en UTF-8 avec indent=2 pour relecture
    humaine ; le format reste JSON strict.
    """
    if base_dir is None:
        base_dir = DEFAULT_EVAL_REPORTS_DIR
    if now is None:
        now = _utc_now()

    path = build_report_path(model=report.model, now=now, base_dir=base_dir)
    base_dir.mkdir(parents=True, exist_ok=True)
    payload = _enrich_report(report, now=now)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


# ---------------------------------------------------------------------------
# run_and_save — orchestration complète
# ---------------------------------------------------------------------------

def run_and_save(
    *,
    model: Optional[str] = None,
    generate_fn: Optional[Callable[[str, str], str]] = None,
    cases: Optional[tuple[EvalCase, ...]] = None,
    base_dir: Optional[Path] = None,
    now: Optional[datetime] = None,
) -> tuple[HarnessReport, Path]:
    """
    Exécute le harness puis sauvegarde le rapport.

    - `generate_fn` injecté → run hors-ligne (tests).
    - `generate_fn=None` → appel Ollama réel via l'extractor.
    - `model=None` → lit `AAC_BLENDER_LLM_MODEL` (défaut `qwen2.5-coder:7b`).

    Retourne (report, path_du_rapport_écrit).
    """
    if model is None:
        model = get_blender_llm_model()

    report = run_harness(
        generate_fn=generate_fn,
        model=model,
        cases=cases if cases is not None else DEFAULT_CASES,
    )
    path = save_report(report, base_dir=base_dir, now=now)
    return report, path


# ---------------------------------------------------------------------------
# Affichage console — réutilisé par CLI et tests éventuels
# ---------------------------------------------------------------------------

def format_summary(report: HarnessReport, path: Path) -> str:
    """Texte synthétique multi-lignes. Pure : pas d'I/O."""
    lines: list[str] = []
    lines.append(f"model              : {report.model}")
    lines.append(f"report             : {path}")
    lines.append(f"total_cases        : {report.total_cases}")
    lines.append(f"parse_ok_rate      : {report.parse_ok_rate:.3f}")
    lines.append(f"mean_score         : {report.mean_score:.3f}")
    lines.append("per_field_accuracy :")
    for k, v in report.per_field_accuracy.items():
        lines.append(f"  {k:<28} {v:.3f}")
    lines.append("case_scores :")
    for s in report.case_scores:
        flag = "OK " if s.parse_ok else "FB "
        lines.append(f"  {flag} {s.case_id:<48} {s.score:.3f}")
    return "\n".join(lines)


# ===========================================================================
# H.6.5.b — Multi-run aggregation
# ===========================================================================
#
# Un single-run est trop bruité pour décider de l'effet d'un changement
# (variance LLM observée jusqu'à ±0.13 sur parse_ok_rate en H.6.4). Cette
# section ajoute la possibilité d'exécuter le harness N fois et de
# produire un rapport agrégé unique, JSON-sérialisable, sauvegardé sous
# `outputs/blender/_eval_reports/{ts}_{model}_xNruns.json`.
#
# Le rapport multi-run a `report_schema_version="2"`, distinct de la
# version "1" du single-run. Les consommateurs peuvent router sur ce
# champ.


def _stats_block(values: Sequence[float]) -> dict[str, float]:
    """
    Calcule mean / min / max / stdev (population) d'une liste de valeurs.

    Pure. Retourne 0.0 partout si `values` est vide ; stdev=0.0 pour N=1
    (via `pstdev`).
    """
    if not values:
        return {"mean": 0.0, "min": 0.0, "max": 0.0, "stdev": 0.0}
    return {
        "mean": statistics.mean(values),
        "min": min(values),
        "max": max(values),
        "stdev": statistics.pstdev(values),  # accepte N=1 → 0.0
    }


def aggregate_multirun(
    reports: Sequence[HarnessReport],
    *,
    cases: Optional[tuple[EvalCase, ...]] = None,
) -> dict[str, Any]:
    """
    Agrège N `HarnessReport` produits par le MÊME corpus en un dict prêt
    à sérialiser. Pure.

    Hypothèses :
    - tous les rapports proviennent du même `cases` (même ordre, mêmes ids) ;
    - même modèle (informatif uniquement, on remonte le 1er).

    Structure de sortie :
      {
        "n_runs", "n_cases",
        "aggregate": {parse_ok_rate, mean_score, per_field_accuracy: {field: stats}},
        "case_aggregates": [{case_id, parse_ok_count, score: stats}, ...],
        "per_run_summaries": [{run_index, parse_ok_rate, mean_score, per_field_accuracy, case_results: [...]}],
        "common_errors": [{error_prefix, count}],
      }
    """
    n_runs = len(reports)
    if n_runs == 0:
        return {
            "n_runs": 0,
            "n_cases": 0,
            "aggregate": {
                "parse_ok_rate": _stats_block([]),
                "mean_score": _stats_block([]),
                "per_field_accuracy": {},
            },
            "case_aggregates": [],
            "per_run_summaries": [],
            "common_errors": [],
        }

    # n_cases : tous les rapports DOIVENT avoir le même nombre de cas.
    n_cases = reports[0].total_cases
    for r in reports[1:]:
        if r.total_cases != n_cases:
            raise ValueError(
                f"aggregate_multirun: rapports incohérents "
                f"(total_cases varie : {n_cases} vs {r.total_cases})"
            )

    # 1. Agrégat top-level : parse_ok_rate, mean_score sur les N runs.
    parse_ok_rates = [r.parse_ok_rate for r in reports]
    mean_scores = [r.mean_score for r in reports]

    # 2. per_field_accuracy : union des clés observées, valeur=0.0 pour les
    #    runs où la clé est absente (cas vide), même si le corpus stable
    #    garantit normalement les mêmes clés à chaque run.
    all_field_keys: set[str] = set()
    for r in reports:
        all_field_keys.update(r.per_field_accuracy.keys())
    per_field_stats: dict[str, dict[str, float]] = {}
    for key in sorted(all_field_keys):
        values = [r.per_field_accuracy.get(key, 0.0) for r in reports]
        per_field_stats[key] = _stats_block(values)

    # 3. case_aggregates : par case_id (basé sur l'ordre du 1er run, identique
    #    aux autres par hypothèse), stats sur scores + comptage parse_ok.
    case_ids = [s.case_id for s in reports[0].case_scores]
    case_aggregates: list[dict[str, Any]] = []
    for idx, case_id in enumerate(case_ids):
        # Cohérence cross-run : même case_id à la même position.
        for r in reports[1:]:
            if r.case_scores[idx].case_id != case_id:
                raise ValueError(
                    f"aggregate_multirun: case_id différent à l'index {idx} "
                    f"({case_id!r} vs {r.case_scores[idx].case_id!r})"
                )
        scores = [r.case_scores[idx].score for r in reports]
        parse_ok_count = sum(1 for r in reports if r.case_scores[idx].parse_ok)
        case_aggregates.append({
            "case_id": case_id,
            "parse_ok_count": parse_ok_count,
            "score": _stats_block(scores),
        })

    # 4. per_run_summaries : détail run par run pour traçabilité.
    per_run_summaries: list[dict[str, Any]] = []
    for i, r in enumerate(reports):
        per_run_summaries.append({
            "run_index": i,
            "parse_ok_rate": r.parse_ok_rate,
            "mean_score": r.mean_score,
            "per_field_accuracy": dict(r.per_field_accuracy),
            "case_results": [
                {
                    "case_id": s.case_id,
                    "parse_ok": s.parse_ok,
                    "score": s.score,
                    "error": s.error,
                }
                for s in r.case_scores
            ],
        })

    # 5. common_errors : préfixes d'erreur (avant le premier ':') triés
    #    par fréquence décroissante. Aide au diagnostic.
    error_counter: Counter[str] = Counter()
    for r in reports:
        for s in r.case_scores:
            if s.error:
                # On garde uniquement le préfixe avant ":" pour grouper
                # "pydantic_validation_error: ..." de toutes les variantes.
                prefix = s.error.split(":", 1)[0].strip()
                error_counter[prefix] += 1
    common_errors = [
        {"error_prefix": k, "count": v}
        for k, v in error_counter.most_common()
    ]

    return {
        "n_runs": n_runs,
        "n_cases": n_cases,
        "aggregate": {
            "parse_ok_rate": _stats_block(parse_ok_rates),
            "mean_score": _stats_block(mean_scores),
            "per_field_accuracy": per_field_stats,
        },
        "case_aggregates": case_aggregates,
        "per_run_summaries": per_run_summaries,
        "common_errors": common_errors,
    }


def build_multirun_report_path(
    *,
    model: str,
    n_runs: int,
    now: datetime,
    base_dir: Path = DEFAULT_EVAL_REPORTS_DIR,
) -> Path:
    """Chemin canonique d'un rapport multi-run. Suffixe `_x{N}runs.json`."""
    fname = (
        f"{_format_timestamp(now)}_{slugify_model(model)}"
        f"_x{n_runs}runs.json"
    )
    return base_dir / fname


def run_and_save_multi(
    *,
    n_runs: int,
    model: Optional[str] = None,
    generate_fn: Optional[Callable[[str, str], str]] = None,
    cases: Optional[tuple[EvalCase, ...]] = None,
    base_dir: Optional[Path] = None,
    now: Optional[datetime] = None,
) -> tuple[dict[str, Any], Path]:
    """
    Exécute le harness N fois et persiste un rapport agrégé.

    Retourne (payload_agrégé, chemin_écrit).

    Quand `generate_fn` est injecté (tests), le bench est purement local.
    Quand `generate_fn=None`, l'extracteur appelle Ollama via le wrapper
    stabilisé H.6.5.a.

    Pour N=1, le rapport multi-run a la même information qu'un single-run
    (stdev=0 partout) mais reste dans le format multi-run. Le single-run
    classique reste accessible via `run_and_save`.
    """
    if n_runs < 1:
        raise ValueError(f"n_runs doit être >= 1, got {n_runs}")
    if model is None:
        model = get_blender_llm_model()
    if cases is None:
        cases = DEFAULT_CASES
    if now is None:
        now = _utc_now()
    if base_dir is None:
        base_dir = DEFAULT_EVAL_REPORTS_DIR

    reports: list[HarnessReport] = []
    for _ in range(n_runs):
        report = run_harness(
            generate_fn=generate_fn,
            model=model,
            cases=cases,
        )
        reports.append(report)

    aggregated = aggregate_multirun(reports, cases=cases)
    payload: dict[str, Any] = {
        "report_schema_version": "2",
        "timestamp": now.isoformat(),
        "model": model,
    }
    payload.update(aggregated)

    base_dir.mkdir(parents=True, exist_ok=True)
    path = build_multirun_report_path(
        model=model, n_runs=n_runs, now=now, base_dir=base_dir,
    )
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return payload, path


def format_summary_multirun(payload: dict[str, Any], path: Path) -> str:
    """Texte synthétique multi-lignes pour CLI. Pure."""
    agg = payload["aggregate"]
    lines: list[str] = []
    lines.append(f"model              : {payload['model']}")
    lines.append(f"report             : {path}")
    lines.append(f"n_runs             : {payload['n_runs']}")
    lines.append(f"n_cases            : {payload['n_cases']}")
    pr = agg["parse_ok_rate"]
    ms = agg["mean_score"]
    lines.append(
        f"parse_ok_rate      : mean={pr['mean']:.3f}  "
        f"min={pr['min']:.3f}  max={pr['max']:.3f}  stdev={pr['stdev']:.3f}"
    )
    lines.append(
        f"mean_score         : mean={ms['mean']:.3f}  "
        f"min={ms['min']:.3f}  max={ms['max']:.3f}  stdev={ms['stdev']:.3f}"
    )
    lines.append("per_field_accuracy (mean) :")
    for k, stats in agg["per_field_accuracy"].items():
        lines.append(
            f"  {k:<28} mean={stats['mean']:.3f}  stdev={stats['stdev']:.3f}"
        )
    lines.append("case_aggregates (score mean) :")
    for c in payload["case_aggregates"]:
        s = c["score"]
        lines.append(
            f"  {c['case_id']:<48} mean={s['mean']:.3f}  "
            f"min={s['min']:.3f}  max={s['max']:.3f}  "
            f"parse_ok={c['parse_ok_count']}/{payload['n_runs']}"
        )
    if payload.get("common_errors"):
        lines.append("common_errors :")
        for e in payload["common_errors"]:
            lines.append(f"  {e['error_prefix']:<32} count={e['count']}")
    return "\n".join(lines)


# ===========================================================================
# H.6.7a — Multi-seed robustness
# ===========================================================================
#
# Sans cette section, le bench multi-run mesure N fois le MÊME seed
# (déterminisme post-H.6.5.a). Cela révèle la stabilité d'inférence
# mais ne dit rien de la robustesse du modèle au-delà de seed=42.
#
# Le multi-seed exécute le corpus une fois par seed avec un
# `generate_fn` câblé sur ce seed (les autres paramètres d'inférence
# restent ceux d'`EXTRACTION_INFERENCE_OPTIONS`). On agrège ensuite
# mean/min/max/stdev sur les N seeds pour distinguer :
#  - un mean_score robuste (faible stdev cross-seed) ;
#  - un mean_score fragile (stdev élevé : un autre seed donnerait
#    une mesure différente).
#
# Schema rapport : `report_schema_version="3"`. Fichier suffixé
# `_x{N}seeds.json` pour ne pas se confondre avec `_xNruns.json`.

# Set de seeds canonique par défaut. Inclut 42 pour traçabilité
# directe avec la baseline H.6.6. Les autres sont diversifiés en
# magnitude pour exercer la diversité de l'échantillonnage Ollama.
DEFAULT_SEEDS: tuple[int, ...] = (42, 7, 1, 123, 999)


def aggregate_multiseed(
    seeds: Sequence[int],
    reports: Sequence[HarnessReport],
    *,
    cases: Optional[tuple[EvalCase, ...]] = None,
) -> dict[str, Any]:
    """
    Agrège N `HarnessReport` produits sur le MÊME corpus avec N seeds
    distincts. Pure.

    Hypothèses (vérifiées) :
    - `len(seeds) == len(reports)` (1:1) ;
    - tous les rapports proviennent du même corpus (même nb cas, même
      ordre des `case_id`).

    Structure de sortie :
      {
        "seeds": [...],
        "n_seeds", "n_cases",
        "aggregate": {parse_ok_rate, mean_score, per_field_accuracy: {field: stats}},
        "case_aggregates": [{case_id, parse_ok_count, score: stats}, ...],
        "per_seed_summaries": [{seed, parse_ok_rate, mean_score,
                                per_field_accuracy, case_results: [...]}],
        "common_errors": [{error_prefix, count}],
      }
    """
    if len(seeds) != len(reports):
        raise ValueError(
            f"aggregate_multiseed: len(seeds)={len(seeds)} ≠ "
            f"len(reports)={len(reports)}"
        )
    n = len(reports)
    if n == 0:
        return {
            "seeds": [],
            "n_seeds": 0,
            "n_cases": 0,
            "aggregate": {
                "parse_ok_rate": _stats_block([]),
                "mean_score": _stats_block([]),
                "per_field_accuracy": {},
            },
            "case_aggregates": [],
            "per_seed_summaries": [],
            "common_errors": [],
        }

    n_cases = reports[0].total_cases
    for r in reports[1:]:
        if r.total_cases != n_cases:
            raise ValueError(
                f"aggregate_multiseed: rapports incohérents "
                f"(total_cases varie : {n_cases} vs {r.total_cases})"
            )

    parse_ok_rates = [r.parse_ok_rate for r in reports]
    mean_scores = [r.mean_score for r in reports]

    all_field_keys: set[str] = set()
    for r in reports:
        all_field_keys.update(r.per_field_accuracy.keys())
    per_field_stats: dict[str, dict[str, float]] = {
        key: _stats_block([r.per_field_accuracy.get(key, 0.0) for r in reports])
        for key in sorted(all_field_keys)
    }

    case_ids = [s.case_id for s in reports[0].case_scores]
    case_aggregates: list[dict[str, Any]] = []
    for idx, case_id in enumerate(case_ids):
        for r in reports[1:]:
            if r.case_scores[idx].case_id != case_id:
                raise ValueError(
                    f"aggregate_multiseed: case_id différent à l'index {idx} "
                    f"({case_id!r} vs {r.case_scores[idx].case_id!r})"
                )
        scores = [r.case_scores[idx].score for r in reports]
        parse_ok_count = sum(1 for r in reports if r.case_scores[idx].parse_ok)
        case_aggregates.append({
            "case_id": case_id,
            "parse_ok_count": parse_ok_count,
            "score": _stats_block(scores),
        })

    per_seed_summaries: list[dict[str, Any]] = []
    for seed, r in zip(seeds, reports):
        per_seed_summaries.append({
            "seed": seed,
            "parse_ok_rate": r.parse_ok_rate,
            "mean_score": r.mean_score,
            "per_field_accuracy": dict(r.per_field_accuracy),
            "case_results": [
                {
                    "case_id": s.case_id,
                    "parse_ok": s.parse_ok,
                    "score": s.score,
                    "error": s.error,
                }
                for s in r.case_scores
            ],
        })

    error_counter: Counter[str] = Counter()
    for r in reports:
        for s in r.case_scores:
            if s.error:
                prefix = s.error.split(":", 1)[0].strip()
                error_counter[prefix] += 1
    common_errors = [
        {"error_prefix": k, "count": v}
        for k, v in error_counter.most_common()
    ]

    return {
        "seeds": list(seeds),
        "n_seeds": n,
        "n_cases": n_cases,
        "aggregate": {
            "parse_ok_rate": _stats_block(parse_ok_rates),
            "mean_score": _stats_block(mean_scores),
            "per_field_accuracy": per_field_stats,
        },
        "case_aggregates": case_aggregates,
        "per_seed_summaries": per_seed_summaries,
        "common_errors": common_errors,
    }


def build_multiseed_report_path(
    *,
    model: str,
    n_seeds: int,
    now: datetime,
    base_dir: Path = DEFAULT_EVAL_REPORTS_DIR,
) -> Path:
    """Chemin canonique d'un rapport multi-seed. Suffixe `_x{N}seeds.json`."""
    fname = (
        f"{_format_timestamp(now)}_{slugify_model(model)}"
        f"_x{n_seeds}seeds.json"
    )
    return base_dir / fname


def run_and_save_multiseed(
    *,
    seeds: Sequence[int],
    model: Optional[str] = None,
    generate_fn_factory: Optional[Callable[[int], Callable[[str, str], str]]] = None,
    cases: Optional[tuple[EvalCase, ...]] = None,
    base_dir: Optional[Path] = None,
    now: Optional[datetime] = None,
) -> tuple[dict[str, Any], Path]:
    """
    Exécute le harness une fois par seed et persiste un rapport agrégé.

    - `seeds` : iterable d'entiers (typiquement `DEFAULT_SEEDS`).
    - `generate_fn_factory` : `seed -> Callable[(model, prompt), str]`.
      Si None, utilise `build_extraction_generate_fn` (Ollama réel,
      inférence stabilisée H.6.5.a, seed surchargé). Permet aux tests
      de fournir un factory qui renvoie un mock par seed sans toucher
      à Ollama.

    Retourne `(payload_agrégé, chemin_écrit)`. Le rapport a
    `report_schema_version="3"`.
    """
    seeds_t = tuple(seeds)
    if not seeds_t:
        raise ValueError("seeds must be a non-empty sequence")
    if len(set(seeds_t)) != len(seeds_t):
        raise ValueError(f"seeds must be unique, got {seeds_t}")

    if model is None:
        model = get_blender_llm_model()
    if cases is None:
        cases = DEFAULT_CASES
    if now is None:
        now = _utc_now()
    if base_dir is None:
        base_dir = DEFAULT_EVAL_REPORTS_DIR
    if generate_fn_factory is None:
        generate_fn_factory = build_extraction_generate_fn

    reports: list[HarnessReport] = []
    for seed in seeds_t:
        gen = generate_fn_factory(seed)
        reports.append(run_harness(generate_fn=gen, model=model, cases=cases))

    aggregated = aggregate_multiseed(seeds_t, reports, cases=cases)
    payload: dict[str, Any] = {
        "report_schema_version": "3",
        "timestamp": now.isoformat(),
        "model": model,
    }
    payload.update(aggregated)

    base_dir.mkdir(parents=True, exist_ok=True)
    path = build_multiseed_report_path(
        model=model, n_seeds=len(seeds_t), now=now, base_dir=base_dir,
    )
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return payload, path


def format_summary_multiseed(payload: dict[str, Any], path: Path) -> str:
    """Texte synthétique multi-lignes pour CLI. Pure."""
    agg = payload["aggregate"]
    lines: list[str] = []
    lines.append(f"model              : {payload['model']}")
    lines.append(f"report             : {path}")
    lines.append(f"seeds              : {payload['seeds']}")
    lines.append(f"n_cases            : {payload['n_cases']}")
    pr = agg["parse_ok_rate"]
    ms = agg["mean_score"]
    lines.append(
        f"parse_ok_rate      : mean={pr['mean']:.3f}  "
        f"min={pr['min']:.3f}  max={pr['max']:.3f}  stdev={pr['stdev']:.3f}"
    )
    lines.append(
        f"mean_score         : mean={ms['mean']:.3f}  "
        f"min={ms['min']:.3f}  max={ms['max']:.3f}  stdev={ms['stdev']:.3f}"
    )
    lines.append("per_field_accuracy (mean across seeds) :")
    for k, stats in agg["per_field_accuracy"].items():
        lines.append(
            f"  {k:<28} mean={stats['mean']:.3f}  stdev={stats['stdev']:.3f}"
        )
    lines.append("case_aggregates (score mean across seeds) :")
    for c in payload["case_aggregates"]:
        s = c["score"]
        lines.append(
            f"  {c['case_id']:<48} mean={s['mean']:.3f}  "
            f"min={s['min']:.3f}  max={s['max']:.3f}  "
            f"parse_ok={c['parse_ok_count']}/{payload['n_seeds']}"
        )
    lines.append("per_seed_summaries :")
    for s in payload["per_seed_summaries"]:
        lines.append(
            f"  seed={s['seed']:<6} parse_ok_rate={s['parse_ok_rate']:.3f}  "
            f"mean_score={s['mean_score']:.3f}"
        )
    if payload.get("common_errors"):
        lines.append("common_errors :")
        for e in payload["common_errors"]:
            lines.append(f"  {e['error_prefix']:<32} count={e['count']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI __main__ (non testée ; appelle Ollama réel)
# ---------------------------------------------------------------------------

def _main(argv: Optional[list[str]] = None) -> int:  # pragma: no cover
    parser = argparse.ArgumentParser(
        description="Bench réel Ollama du harness Product Render IR avec "
                    "persistance JSON sous outputs/blender/_eval_reports/. "
                    "Mode single-run (défaut) ou multi-run (--runs N).",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Nom du modèle Ollama. Défaut : AAC_BLENDER_LLM_MODEL "
             "(qwen2.5-coder:7b si non positionné).",
    )
    parser.add_argument(
        "--base-dir",
        default=None,
        help=f"Répertoire de sortie. Défaut : {DEFAULT_EVAL_REPORTS_DIR}.",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=1,
        help="Nombre d'exécutions du harness avec le seed figé (H.6.5.a). "
             ">1 produit un rapport multi-run agrégé (schema v2, stdev≈0 "
             "vu le déterminisme). Défaut : 1.",
    )
    parser.add_argument(
        "--seeds",
        type=str,
        default=None,
        help="Liste de seeds entiers séparés par des virgules, ex. "
             "'42,7,1,123,999'. Produit un rapport multi-seed agrégé "
             "(schema v3). Mutuellement exclusif avec --runs>1. "
             f"Defaults canoniques : {','.join(str(s) for s in DEFAULT_SEEDS)}.",
    )
    parser.add_argument(
        "--multi-seed",
        action="store_true",
        help=f"Raccourci : utilise les seeds canoniques par défaut "
             f"({DEFAULT_SEEDS}). Équivalent à --seeds "
             f"'{','.join(str(s) for s in DEFAULT_SEEDS)}'.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="N'imprime que le chemin du rapport.",
    )
    args = parser.parse_args(argv)

    base_dir = Path(args.base_dir) if args.base_dir else None

    # Résolution du mode (single / multi-run / multi-seed).
    multi_seed_active = bool(args.seeds or args.multi_seed)
    if multi_seed_active and args.runs > 1:
        parser.error(
            "--seeds / --multi-seed et --runs>1 sont mutuellement exclusifs."
        )

    if multi_seed_active:
        if args.seeds:
            try:
                seeds = tuple(
                    int(s.strip()) for s in args.seeds.split(",") if s.strip()
                )
            except ValueError as exc:
                parser.error(f"--seeds: liste invalide : {exc}")
            if not seeds:
                parser.error("--seeds: liste vide")
        else:
            seeds = DEFAULT_SEEDS
        payload, path = run_and_save_multiseed(
            seeds=seeds, model=args.model, base_dir=base_dir,
        )
        if args.quiet:
            print(path)
        else:
            print(format_summary_multiseed(payload, path))
    elif args.runs > 1:
        payload, path = run_and_save_multi(
            n_runs=args.runs, model=args.model, base_dir=base_dir,
        )
        if args.quiet:
            print(path)
        else:
            print(format_summary_multirun(payload, path))
    else:
        report, path = run_and_save(model=args.model, base_dir=base_dir)
        if args.quiet:
            print(path)
        else:
            print(format_summary(report, path))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
