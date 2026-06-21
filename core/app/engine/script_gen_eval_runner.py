"""
H.6.8.a / H.6.8.b — Runner d'eval persistant pour `script_gen`.

Encapsule l'exécution du harness et la **sauvegarde** d'un rapport
JSON timestampé sous `outputs/blender/_eval_reports/`. Permet d'établir
une baseline reproductible pour le second site LLM de la pipeline Blender.

Séparation des responsabilités :
- `script_gen_eval_harness` : mesure (corpus, scoring, agrégation).
- `script_gen_eval_runner`  : exécution datée + persistance + multi-run.

Modes :
- single-run (H.6.8.a, schema `"script_gen.1"`) : un run unique sur le
  corpus, rapport `<isots>_script_gen_<model>.json`.
- multi-run  (H.6.8.b.2, schema `"script_gen.2"`) : N runs successifs sur
  le MÊME corpus pour mesurer la stabilité d'inférence. Rapport
  `<isots>_script_gen_<model>_x{N}runs.json` avec mean/min/max/stdev
  par cas et global.

Persistance des scripts bruts (H.6.8.b.1) :
- Chaque run écrit le `scene.py` extrait sous
  `<base_dir>/<run_dir>/<case_id>.py`. `raw_script_path` (relatif à
  `base_dir`) est joint à chaque case dans le rapport JSON pour
  permettre inspection humaine post-bench sans recharger le LLM.

Garanties :
- Pure quand `generate_fn` est fourni → testable hors-ligne.
- Aucune mutation runtime : ne touche ni router/planner/executor, ni
  builder, ni IR, ni modèle par défaut, ni Blender, ni Ollama (sauf
  bench manuel).
- Conventions de nommage fichier stables (tri lexico = tri chrono).

Exécution réelle (manuelle, hors CI, host avec Ollama local) :
    python -m app.engine.script_gen_eval_runner
    python -m app.engine.script_gen_eval_runner --model qwen2.5-coder:7b
    python -m app.engine.script_gen_eval_runner --runs 3
    python -m app.engine.script_gen_eval_runner --base-dir my_reports
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import statistics
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from app.engine.blender_model_config import get_blender_llm_model
from app.engine.script_gen_eval_cases import DEFAULT_CASES, ScriptGenCase
from app.engine.script_gen_eval_harness import (
    GenerateFn,
    ScriptGenCaseScore,
    ScriptGenHarnessReport,
    case_score_to_dict,
    run_harness,
)


# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

DEFAULT_EVAL_REPORTS_DIR = Path("outputs/blender/_eval_reports")
REPORT_SCHEMA = "script_gen.1"
REPORT_SCHEMA_MULTIRUN = "script_gen.2"

# Slug filesystem-safe pour le nom de modèle (cohérent avec
# product_render_eval_runner). Autorise lettres/chiffres/`.`/`-`.
_SLUG_BAD_CHARS = re.compile(r"[^A-Za-z0-9.\-]+")


# ---------------------------------------------------------------------------
# Helpers nommage / chemin
# ---------------------------------------------------------------------------

def slugify_model(model: str) -> str:
    """Slug filesystem-safe à partir du nom de modèle."""
    if not model or not model.strip():
        return "unknown-model"
    slug = _SLUG_BAD_CHARS.sub("-", model.strip())
    slug = slug.strip("-")
    return slug or "unknown-model"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _format_timestamp(now: datetime) -> str:
    """`YYYY-MM-DDTHHMMSSZ` : filesystem-safe, tri lexico = chrono."""
    return now.strftime("%Y-%m-%dT%H%M%SZ")


def build_report_path(
    *,
    model: str,
    now: datetime,
    base_dir: Path = DEFAULT_EVAL_REPORTS_DIR,
) -> Path:
    """Chemin canonique d'un rapport script_gen single-run."""
    fname = f"{_format_timestamp(now)}_script_gen_{slugify_model(model)}.json"
    return base_dir / fname


def build_multirun_report_path(
    *,
    model: str,
    n_runs: int,
    now: datetime,
    base_dir: Path = DEFAULT_EVAL_REPORTS_DIR,
) -> Path:
    """Chemin canonique d'un rapport script_gen multi-run (H.6.8.b.2)."""
    fname = (
        f"{_format_timestamp(now)}_script_gen_{slugify_model(model)}"
        f"_x{n_runs}runs.json"
    )
    return base_dir / fname


def build_scripts_dir_name(
    *,
    model: str,
    now: datetime,
    n_runs: int | None = None,
) -> str:
    """
    Nom du dossier scripts associé à un rapport.

    - single-run : `<timestamp>_script_gen_<model>_scripts`
    - multi-run  : `<timestamp>_script_gen_<model>_x{N}runs_scripts`

    Retourne un nom RELATIF (pas un Path absolu), pour faciliter le
    `raw_script_path` relatif du rapport JSON.
    """
    base = f"{_format_timestamp(now)}_script_gen_{slugify_model(model)}"
    if n_runs is None:
        return f"{base}_scripts"
    return f"{base}_x{n_runs}runs_scripts"


def _prompt_sha256_short(prompt: str, length: int = 12) -> str:
    """Hash court d'un prompt pour traçabilité sans bloat du rapport."""
    h = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    return h[:length]


# ---------------------------------------------------------------------------
# H.6.8.b.1 — Persistance des scripts bruts
# ---------------------------------------------------------------------------

def _safe_case_filename(case_id: str) -> str:
    """
    Sécurise un case_id pour usage filesystem (devrait déjà être OK,
    `[a-z0-9_-]+` par convention corpus, mais on filtre par sûreté).
    """
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", case_id).strip("-")
    return cleaned or "unknown-case"


def persist_extracted_scripts(
    scores: Sequence[ScriptGenCaseScore],
    scripts_dir: Path,
) -> dict[str, str]:
    """
    Écrit chaque `score.extracted_code` (s'il existe) sous
    `scripts_dir/<case_id>.py`. Retourne un dict
    `{case_id: <relative_filename>}` pour fixer `raw_script_path`
    dans le rapport JSON.

    Si `extracted_code` est None ou vide pour un cas, on n'écrit rien
    pour ce cas (et la clé n'apparaît pas dans le dict retourné).

    Pure côté logique : I/O encapsulée, pas de couplage harness.
    """
    scripts_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, str] = {}
    for score in scores:
        if not score.extracted_code:
            continue
        fname = f"{_safe_case_filename(score.case_id)}.py"
        target = scripts_dir / fname
        target.write_text(score.extracted_code, encoding="utf-8")
        written[score.case_id] = fname  # relative au scripts_dir
    return written


# ---------------------------------------------------------------------------
# Sérialisation du rapport final (avec header schema + metadata)
# ---------------------------------------------------------------------------

def _inference_config_block() -> dict[str, Any]:
    """
    Bloc `inference_config` du rapport. H.6.8.b.2 expose les paramètres
    stabilisés réels (cf. SCRIPT_GEN_INFERENCE_OPTIONS dans le harness).
    """
    from app.engine.script_gen_eval_harness import SCRIPT_GEN_INFERENCE_OPTIONS

    return {
        "temperature": SCRIPT_GEN_INFERENCE_OPTIONS["temperature"],
        "top_p": SCRIPT_GEN_INFERENCE_OPTIONS["top_p"],
        "top_k": SCRIPT_GEN_INFERENCE_OPTIONS["top_k"],
        "seed": SCRIPT_GEN_INFERENCE_OPTIONS["seed"],
        "format": None,  # script_gen produit du Python markdown, pas du JSON.
        "num_ctx": SCRIPT_GEN_INFERENCE_OPTIONS["num_ctx"],
        "notes": (
            "H.6.8.b.2 — inférence stabilisée (temperature=0, top_k=1, "
            "top_p=1, seed=42, num_ctx=8192). PAS de format=json (le LLM "
            "produit du Python markdown). Le seed est partagé entre runs "
            "single — pour la robustesse cross-seed, utiliser une factory "
            "build_script_gen_generate_fn(seed) (non câblée par la CLI "
            "single en H.6.8.b)."
        ),
    }


def build_report_payload(
    report: ScriptGenHarnessReport,
    *,
    cases: Sequence[ScriptGenCase],
    now: datetime,
    scripts_dir_name: str | None = None,
    raw_script_filenames: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """
    Construit le dict JSON final à persister, avec header schema +
    metadata corpus + hashs prompts.

    Args:
        report: rapport harness.
        cases: corpus correspondant.
        now: timestamp.
        scripts_dir_name: nom RELATIF du dossier scripts (joint à
            `raw_script_path` de chaque case).
        raw_script_filenames: dict `case_id -> filename` retourné par
            `persist_extracted_scripts` (relatif au scripts_dir).
    """
    if len(cases) != len(report.cases):
        raise ValueError(
            f"build_report_payload: mismatch cases ({len(cases)}) vs "
            f"report.cases ({len(report.cases)})"
        )

    raw_script_filenames = raw_script_filenames or {}

    cases_payload: list[dict[str, Any]] = []
    for case, score in zip(cases, report.cases):
        case_dict = case_score_to_dict(score)
        case_dict["prompt_sha256_short"] = _prompt_sha256_short(case.prompt)
        # H.6.8.b.1 — raw_script_path relatif au base_dir.
        fname = raw_script_filenames.get(case.id)
        if scripts_dir_name and fname:
            case_dict["raw_script_path"] = f"{scripts_dir_name}/{fname}"
        else:
            case_dict["raw_script_path"] = None
        cases_payload.append(case_dict)

    return {
        "schema": REPORT_SCHEMA,
        "generated_at_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "model": report.model,
        "corpus_version": {
            "n_cases": len(cases),
            "case_ids": [c.id for c in cases],
        },
        "inference_config": _inference_config_block(),
        "cases": cases_payload,
        "aggregate": dict(report.aggregate),
    }


def save_report(
    payload: dict[str, Any],
    path: Path,
) -> None:
    """Écrit le payload JSON sur disque (crée le parent si besoin)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Orchestration : run + save
# ---------------------------------------------------------------------------

def run_and_save(
    *,
    cases: Sequence[ScriptGenCase] = DEFAULT_CASES,
    model: str | None = None,
    generate_fn: Optional[GenerateFn] = None,
    base_dir: Path = DEFAULT_EVAL_REPORTS_DIR,
    now: Optional[datetime] = None,
) -> tuple[Path, dict[str, Any]]:
    """
    Exécute le harness puis sauvegarde le rapport JSON.

    H.6.8.b.1 — Persiste également chaque `scene.py` LLM extrait sous
    `<base_dir>/<scripts_dir_name>/<case_id>.py` et expose
    `raw_script_path` (relatif à `base_dir`) dans le payload JSON.

    Retourne (chemin_rapport, payload).
    """
    when = now if now is not None else _utc_now()
    report = run_harness(cases=cases, model=model, generate_fn=generate_fn)

    # Persistance scripts bruts (H.6.8.b.1).
    scripts_dir_name = build_scripts_dir_name(model=report.model, now=when)
    scripts_dir = base_dir / scripts_dir_name
    raw_script_filenames = persist_extracted_scripts(report.cases, scripts_dir)

    payload = build_report_payload(
        report,
        cases=cases,
        now=when,
        scripts_dir_name=scripts_dir_name,
        raw_script_filenames=raw_script_filenames,
    )
    path = build_report_path(model=report.model, now=when, base_dir=base_dir)
    save_report(payload, path)
    return path, payload


# ---------------------------------------------------------------------------
# H.6.8.b.2 — Multi-run agrégation & orchestration
# ---------------------------------------------------------------------------

def _stats_block(values: Sequence[float]) -> dict[str, float]:
    """Bloc mean/min/max/stdev sur une séquence numérique. N=0 → 0."""
    if not values:
        return {"mean": 0.0, "min": 0.0, "max": 0.0, "stdev": 0.0}
    if len(values) == 1:
        v = float(values[0])
        return {"mean": v, "min": v, "max": v, "stdev": 0.0}
    return {
        "mean": float(statistics.mean(values)),
        "min": float(min(values)),
        "max": float(max(values)),
        "stdev": float(statistics.pstdev(values)),  # population stdev
    }


def aggregate_multirun(
    reports: Sequence[ScriptGenHarnessReport],
    *,
    cases: Sequence[ScriptGenCase],
) -> dict[str, Any]:
    """
    Agrège N rapports single-run produits par le MÊME corpus.

    Structure de sortie :
      {
        "n_runs", "n_cases",
        "aggregate": {
          "mean_score": stats_block,
          "generation_ok_rate": stats_block,
          "ast_parseable_rate": stats_block,
          "template_match_rate": stats_block,
          "mean_duration_seconds": stats_block,
        },
        "case_aggregates": [
          {
            "case_id",
            "score": stats_block,
            "ast_parseable_count": int,  # combien de runs ont passé ast
            "template_required_objects_named_count": int | null,
          },
          ...
        ],
        "per_run_summaries": [
          {
            "run_index",
            "mean_score",
            "generation_ok_rate",
            "ast_parseable_rate",
            "template_match_rate",
            "mean_duration_seconds",
            "case_results": [
              {"case_id", "score", "ast_parseable", "error"}, ...
            ],
          },
          ...
        ],
      }
    """
    n_runs = len(reports)
    n_cases = len(cases)

    if n_runs == 0:
        return {
            "n_runs": 0,
            "n_cases": n_cases,
            "aggregate": {
                "mean_score": _stats_block([]),
                "generation_ok_rate": _stats_block([]),
                "ast_parseable_rate": _stats_block([]),
                "template_match_rate": _stats_block([]),
                "mean_duration_seconds": _stats_block([]),
            },
            "case_aggregates": [],
            "per_run_summaries": [],
        }

    # Cohérence : tous les rapports doivent porter le même nombre de cas.
    for r in reports:
        if len(r.cases) != n_cases:
            raise ValueError(
                f"aggregate_multirun: rapport avec len(cases)="
                f"{len(r.cases)} != attendu {n_cases}"
            )

    # 1. Agrégat top-level (sur N runs).
    mean_scores = [float(r.aggregate.get("mean_score", 0.0)) for r in reports]
    gen_ok_rates = [float(r.aggregate.get("generation_ok_rate", 0.0)) for r in reports]
    ast_rates = [float(r.aggregate.get("ast_parseable_rate", 0.0)) for r in reports]
    tm_rates = [float(r.aggregate.get("template_match_rate", 0.0)) for r in reports]
    durations = [float(r.aggregate.get("mean_duration_seconds", 0.0)) for r in reports]

    # 2. case_aggregates : stats cross-run par case_id (ordre du 1er run).
    case_ids = [s.case_id for s in reports[0].cases]
    case_aggregates: list[dict[str, Any]] = []
    for idx, case_id in enumerate(case_ids):
        # Cohérence cross-run : même case_id à la même position.
        for r in reports[1:]:
            if r.cases[idx].case_id != case_id:
                raise ValueError(
                    f"aggregate_multirun: case_id différent à l'index {idx} "
                    f"({case_id!r} vs {r.cases[idx].case_id!r})"
                )
        scores = [r.cases[idx].score for r in reports]
        ast_ok_count = sum(1 for r in reports if r.cases[idx].ast_parseable)
        # Pour le check template_required_objects, on compte les runs où
        # TOUS les objets requis sont nommés (template_required_objects_named
        # est un dict[obj_name -> bool] ou None).
        trn_count: int | None
        if reports[0].cases[idx].template_required_objects_named is None:
            trn_count = None
        else:
            trn_count = sum(
                1
                for r in reports
                if r.cases[idx].template_required_objects_named
                and all(r.cases[idx].template_required_objects_named.values())
            )
        case_aggregates.append({
            "case_id": case_id,
            "score": _stats_block(scores),
            "ast_parseable_count": ast_ok_count,
            "template_required_objects_named_count": trn_count,
        })

    # 3. per_run_summaries : détail run par run pour traçabilité.
    per_run_summaries: list[dict[str, Any]] = []
    for i, r in enumerate(reports):
        per_run_summaries.append({
            "run_index": i,
            "mean_score": float(r.aggregate.get("mean_score", 0.0)),
            "generation_ok_rate": float(r.aggregate.get("generation_ok_rate", 0.0)),
            "ast_parseable_rate": float(r.aggregate.get("ast_parseable_rate", 0.0)),
            "template_match_rate": float(r.aggregate.get("template_match_rate", 0.0)),
            "mean_duration_seconds": float(
                r.aggregate.get("mean_duration_seconds", 0.0)
            ),
            "case_results": [
                {
                    "case_id": s.case_id,
                    "score": s.score,
                    "ast_parseable": s.ast_parseable,
                    "error": s.error,
                }
                for s in r.cases
            ],
        })

    return {
        "n_runs": n_runs,
        "n_cases": n_cases,
        "aggregate": {
            "mean_score": _stats_block(mean_scores),
            "generation_ok_rate": _stats_block(gen_ok_rates),
            "ast_parseable_rate": _stats_block(ast_rates),
            "template_match_rate": _stats_block(tm_rates),
            "mean_duration_seconds": _stats_block(durations),
        },
        "case_aggregates": case_aggregates,
        "per_run_summaries": per_run_summaries,
    }


def build_multirun_payload(
    reports: Sequence[ScriptGenHarnessReport],
    *,
    cases: Sequence[ScriptGenCase],
    now: datetime,
    n_runs: int,
    model: str,
    scripts_dir_name: str | None = None,
    raw_script_filenames_per_run: Sequence[Mapping[str, str]] | None = None,
) -> dict[str, Any]:
    """
    Compose le payload JSON multi-run (schema `script_gen.2`).

    Si `scripts_dir_name` + `raw_script_filenames_per_run` fournis,
    expose `raw_script_path` dans chaque `per_run_summaries[i].case_results[j]`
    avec le chemin relatif `<scripts_dir_name>/run{i}/<case_id>.py`.
    """
    aggregated = aggregate_multirun(reports, cases=cases)

    if raw_script_filenames_per_run and scripts_dir_name:
        for i, summary in enumerate(aggregated["per_run_summaries"]):
            fnames = raw_script_filenames_per_run[i] if i < len(raw_script_filenames_per_run) else {}
            for case_result in summary["case_results"]:
                cid = case_result["case_id"]
                fname = fnames.get(cid)
                if fname:
                    case_result["raw_script_path"] = (
                        f"{scripts_dir_name}/run{i}/{fname}"
                    )
                else:
                    case_result["raw_script_path"] = None

    return {
        "schema": REPORT_SCHEMA_MULTIRUN,
        "generated_at_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "model": model,
        "n_runs": n_runs,
        "corpus_version": {
            "n_cases": len(cases),
            "case_ids": [c.id for c in cases],
        },
        "inference_config": _inference_config_block(),
        **aggregated,
    }


def run_and_save_multi(
    *,
    n_runs: int,
    cases: Sequence[ScriptGenCase] = DEFAULT_CASES,
    model: str | None = None,
    generate_fn: Optional[GenerateFn] = None,
    base_dir: Path = DEFAULT_EVAL_REPORTS_DIR,
    now: Optional[datetime] = None,
) -> tuple[Path, dict[str, Any]]:
    """
    Exécute le harness N fois sur le MÊME corpus et persiste un rapport
    multi-run agrégé. Persiste également les scripts de chaque run sous
    `<base_dir>/<scripts_dir>/run{i}/<case_id>.py`.

    Retourne (chemin_rapport, payload).

    Pour N=1, le rapport est sémantiquement équivalent à un single-run
    mais reste au format multi-run (stdev=0 partout). Préférer
    `run_and_save` pour N=1 si on veut le schema `"script_gen.1"`.
    """
    if n_runs < 1:
        raise ValueError(f"n_runs doit être >= 1, got {n_runs}")

    when = now if now is not None else _utc_now()

    # On résout le model une fois pour stabiliser les noms de fichier.
    resolved_model = model if model is not None else get_blender_llm_model()

    scripts_dir_name = build_scripts_dir_name(
        model=resolved_model, now=when, n_runs=n_runs
    )

    reports: list[ScriptGenHarnessReport] = []
    raw_script_filenames_per_run: list[dict[str, str]] = []

    for i in range(n_runs):
        report = run_harness(
            cases=cases,
            model=resolved_model,
            generate_fn=generate_fn,
        )
        reports.append(report)

        run_scripts_dir = base_dir / scripts_dir_name / f"run{i}"
        fnames = persist_extracted_scripts(report.cases, run_scripts_dir)
        raw_script_filenames_per_run.append(fnames)

    payload = build_multirun_payload(
        reports,
        cases=cases,
        now=when,
        n_runs=n_runs,
        model=resolved_model,
        scripts_dir_name=scripts_dir_name,
        raw_script_filenames_per_run=raw_script_filenames_per_run,
    )

    path = build_multirun_report_path(
        model=resolved_model, n_runs=n_runs, now=when, base_dir=base_dir,
    )
    save_report(payload, path)
    return path, payload


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="script_gen_eval_runner",
        description=(
            "H.6.8.a/b — Eval harness `script_gen`. Exécute le corpus "
            "canonique contre Ollama et sauvegarde un rapport JSON. "
            "Avec --runs N, mesure la stabilité d'inférence (H.6.8.b.2)."
        ),
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Modèle LLM (défaut: AAC_BLENDER_LLM_MODEL ou qwen2.5-coder:7b).",
    )
    parser.add_argument(
        "--base-dir",
        default=str(DEFAULT_EVAL_REPORTS_DIR),
        help=f"Dossier de sortie (défaut: {DEFAULT_EVAL_REPORTS_DIR}).",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=1,
        help=(
            "Nombre d'exécutions répétées sur le MÊME corpus (H.6.8.b.2). "
            "1 = single-run (schema script_gen.1). N>1 = multi-run "
            "(schema script_gen.2) pour mesurer la stabilité d'inférence."
        ),
    )
    return parser.parse_args(argv)


def _print_single_summary(payload: dict[str, Any], path: Path) -> None:
    aggregate = payload["aggregate"]
    print(f"script_gen eval terminée. Rapport : {path}")
    print(f"  model               : {payload['model']}")
    print(f"  n_cases             : {aggregate['n_cases']}")
    print(f"  mean_score          : {aggregate['mean_score']:.3f}")
    print(f"  generation_ok_rate  : {aggregate['generation_ok_rate']:.3f}")
    print(f"  ast_parseable_rate  : {aggregate['ast_parseable_rate']:.3f}")
    print(f"  template_match_rate : {aggregate['template_match_rate']:.3f}")
    print(f"  total_duration_s    : {aggregate['total_duration_seconds']:.2f}")


def _print_multi_summary(payload: dict[str, Any], path: Path) -> None:
    agg = payload["aggregate"]
    ms = agg["mean_score"]
    print(f"script_gen multi-run terminé. Rapport : {path}")
    print(f"  model               : {payload['model']}")
    print(f"  n_runs              : {payload['n_runs']}")
    print(f"  n_cases             : {payload['n_cases']}")
    print(
        f"  mean_score          : mean={ms['mean']:.3f}  "
        f"min={ms['min']:.3f}  max={ms['max']:.3f}  stdev={ms['stdev']:.3f}"
    )
    gor = agg["generation_ok_rate"]
    print(
        f"  generation_ok_rate  : mean={gor['mean']:.3f}  "
        f"stdev={gor['stdev']:.3f}"
    )
    apr = agg["ast_parseable_rate"]
    print(
        f"  ast_parseable_rate  : mean={apr['mean']:.3f}  "
        f"stdev={apr['stdev']:.3f}"
    )
    print("  per-case score (mean / stdev) :")
    for c in payload["case_aggregates"]:
        s = c["score"]
        print(
            f"    {c['case_id']:<40s} mean={s['mean']:.3f}  "
            f"stdev={s['stdev']:.3f}  ast_ok={c['ast_parseable_count']}/{payload['n_runs']}"
        )
    # Verdict stabilité.
    if ms["stdev"] == 0.0:
        verdict = "STABLE (stdev=0 sur mean_score)"
    elif ms["stdev"] < 0.02:
        verdict = "QUASI-STABLE (stdev<0.02)"
    else:
        verdict = f"INSTABLE (stdev={ms['stdev']:.3f})"
    print(f"  stability_verdict   : {verdict}")


def main(argv: Sequence[str] | None = None) -> int:
    # Lancé en standalone (hors backend), ce runner ne bénéficie pas du
    # load_dotenv() de app/main.py : sans ça, get_ollama_generate_url() retombe
    # sur le défaut localhost:12000 → ConnectionError. On charge donc le .env ici.
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    args = _parse_args(argv if argv is not None else sys.argv[1:])
    model = args.model if args.model else get_blender_llm_model()
    base_dir = Path(args.base_dir)
    n_runs = int(args.runs)
    if n_runs < 1:
        print("script_gen_eval_runner: --runs doit être >= 1", file=sys.stderr)
        return 2

    try:
        if n_runs == 1:
            path, payload = run_and_save(
                model=model,
                base_dir=base_dir,
            )
            _print_single_summary(payload, path)
        else:
            path, payload = run_and_save_multi(
                n_runs=n_runs,
                model=model,
                base_dir=base_dir,
            )
            _print_multi_summary(payload, path)
    except Exception:  # noqa: BLE001
        print("script_gen_eval_runner: échec d'exécution", file=sys.stderr)
        traceback.print_exc()
        return 1

    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
