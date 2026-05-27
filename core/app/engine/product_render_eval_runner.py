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
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from app.engine.blender_model_config import get_blender_llm_model
from app.engine.product_render_eval_cases import DEFAULT_CASES, EvalCase
from app.engine.product_render_eval_harness import (
    HarnessReport,
    report_to_dict,
    run_harness,
)


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


# ---------------------------------------------------------------------------
# CLI __main__ (non testée ; appelle Ollama réel)
# ---------------------------------------------------------------------------

def _main(argv: Optional[list[str]] = None) -> int:  # pragma: no cover
    parser = argparse.ArgumentParser(
        description="Bench réel Ollama du harness Product Render IR avec "
                    "persistance JSON sous outputs/blender/_eval_reports/.",
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
        "--quiet",
        action="store_true",
        help="N'imprime que le chemin du rapport.",
    )
    args = parser.parse_args(argv)

    base_dir = Path(args.base_dir) if args.base_dir else None
    report, path = run_and_save(model=args.model, base_dir=base_dir)

    if args.quiet:
        print(path)
    else:
        print(format_summary(report, path))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
