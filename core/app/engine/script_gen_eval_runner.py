"""
H.6.8.a — Runner d'eval persistant pour `script_gen`.

Encapsule l'exécution du harness H.6.8.a et la **sauvegarde** d'un rapport
JSON timestampé sous `outputs/blender/_eval_reports/`. Permet d'établir
une baseline reproductible pour le second site LLM de la pipeline Blender.

Séparation des responsabilités :
- `script_gen_eval_harness` : mesure (corpus, scoring, agrégation).
- `script_gen_eval_runner`  : exécution datée + persistance.

Garanties :
- Pure quand `generate_fn` est fourni → testable hors-ligne.
- Aucune mutation runtime : ne touche ni router/planner/executor, ni
  builder, ni IR, ni modèle par défaut, ni Blender, ni Ollama (sauf
  bench manuel).
- Conventions de nommage fichier stables (tri lexico = tri chrono).

Exécution réelle (manuelle, hors CI, host avec Ollama local) :
    python -m app.engine.script_gen_eval_runner
    python -m app.engine.script_gen_eval_runner --model qwen2.5-coder:7b
    python -m app.engine.script_gen_eval_runner --base-dir my_reports
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Sequence

from app.engine.blender_model_config import get_blender_llm_model
from app.engine.script_gen_eval_cases import DEFAULT_CASES, ScriptGenCase
from app.engine.script_gen_eval_harness import (
    GenerateFn,
    ScriptGenHarnessReport,
    case_score_to_dict,
    run_harness,
)


# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

DEFAULT_EVAL_REPORTS_DIR = Path("outputs/blender/_eval_reports")
REPORT_SCHEMA = "script_gen.1"

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
    """Chemin canonique d'un rapport script_gen."""
    fname = f"{_format_timestamp(now)}_script_gen_{slugify_model(model)}.json"
    return base_dir / fname


def _prompt_sha256_short(prompt: str, length: int = 12) -> str:
    """Hash court d'un prompt pour traçabilité sans bloat du rapport."""
    h = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    return h[:length]


# ---------------------------------------------------------------------------
# Sérialisation du rapport final (avec header schema + metadata)
# ---------------------------------------------------------------------------

def build_report_payload(
    report: ScriptGenHarnessReport,
    *,
    cases: Sequence[ScriptGenCase],
    now: datetime,
) -> dict[str, Any]:
    """
    Construit le dict JSON final à persister, avec header schema +
    metadata corpus + hashs prompts.
    """
    if len(cases) != len(report.cases):
        raise ValueError(
            f"build_report_payload: mismatch cases ({len(cases)}) vs "
            f"report.cases ({len(report.cases)})"
        )

    cases_payload: list[dict[str, Any]] = []
    for case, score in zip(cases, report.cases):
        case_dict = case_score_to_dict(score)
        case_dict["prompt_sha256_short"] = _prompt_sha256_short(case.prompt)
        cases_payload.append(case_dict)

    return {
        "schema": REPORT_SCHEMA,
        "generated_at_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "model": report.model,
        "corpus_version": {
            "n_cases": len(cases),
            "case_ids": [c.id for c in cases],
        },
        "inference_config": {
            "temperature": None,
            "top_p": None,
            "top_k": None,
            "seed": None,
            "format": None,
            "num_ctx": None,
            "notes": (
                "script_gen utilise les defaults Ollama actuels "
                "(cf. blender_client.py). H.6.8.a ne stabilise PAS "
                "l'inférence — c'est une éventuelle H.6.8.b si la "
                "variance mesurée le justifie."
            ),
        },
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
    Exécute le harness puis sauvegarde le rapport JSON. Retourne
    (chemin_rapport, payload).
    """
    when = now if now is not None else _utc_now()
    report = run_harness(cases=cases, model=model, generate_fn=generate_fn)
    payload = build_report_payload(report, cases=cases, now=when)
    path = build_report_path(model=report.model, now=when, base_dir=base_dir)
    save_report(payload, path)
    return path, payload


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="script_gen_eval_runner",
        description=(
            "H.6.8.a — Eval harness `script_gen`. Exécute le corpus "
            "canonique contre Ollama et sauvegarde un rapport JSON."
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
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    model = args.model if args.model else get_blender_llm_model()
    base_dir = Path(args.base_dir)

    try:
        path, payload = run_and_save(
            model=model,
            base_dir=base_dir,
        )
    except Exception:  # noqa: BLE001
        print("script_gen_eval_runner: échec d'exécution", file=sys.stderr)
        traceback.print_exc()
        return 1

    aggregate = payload["aggregate"]
    print(f"script_gen eval terminée. Rapport : {path}")
    print(f"  model               : {payload['model']}")
    print(f"  n_cases             : {aggregate['n_cases']}")
    print(f"  mean_score          : {aggregate['mean_score']:.3f}")
    print(f"  generation_ok_rate  : {aggregate['generation_ok_rate']:.3f}")
    print(f"  ast_parseable_rate  : {aggregate['ast_parseable_rate']:.3f}")
    print(f"  template_match_rate : {aggregate['template_match_rate']:.3f}")
    print(f"  total_duration_s    : {aggregate['total_duration_seconds']:.2f}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
