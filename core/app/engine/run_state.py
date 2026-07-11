"""
run_state.py — Checkpoint persistant d'un run (chantier 4A).

Photographie l'état d'exécution dans `outputs/runs/<request_id>/state.json`
(le même répertoire que le journal d'événements) après CHAQUE step : un run
interrompu — service tombé, crash, timeout — ne perd plus les steps déjà
payés. `resume_request` (executor) recharge cette photo, restaure les steps
réussis et ne ré-exécute que le reste.

Contenu : la requête d'origine (message/mode/has_image), la décision de
routage, le plan, et les résultats de steps — tout ce qu'il faut pour
reconstruire un ExecutionState équivalent. Le `context` mémoire n'est PAS
sérialisé : les steps le reconstruisent défensivement depuis le message
(comportement existant de step_executor), et les sorties inter-steps
passent par step_results, qui EST restauré.

Garanties (mêmes invariants que run_events / llm_trajectory_log) :
- l'écriture ne lève JAMAIS (un checkpoint perdu est acceptable, un crash
  de pipeline ne l'est pas) ; la lecture retourne None sur tout problème ;
- l'écriture est ATOMIQUE (temporaire même-dossier + fsync + os.replace) :
  un crash ou un disque plein laisse l'ancien state.json intact ou le
  nouveau complet, jamais un JSON tronqué ;
- stdlib uniquement ;
- désactivable via AAC_RUN_STATE_ENABLED (les tests le font par défaut) ;
- la rétention est portée par la purge de run_events (state.json est
  supprimé avec events.jsonl — même classe de données : prompts inclus).
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from app.engine.planner_types import ExecutionPlan, PlanStep, StepResult
from app.engine.run_events import get_run_events_dir
from app.engine.run_identity import resolve_run_dir

RUN_STATE_ENABLED_ENV = "AAC_RUN_STATE_ENABLED"
STATE_FILENAME = "state.json"
STATE_VERSION = 1

_DISABLED_VALUES: frozenset[str] = frozenset({"0", "false", "no", "off"})


def is_run_state_enabled() -> bool:
    raw = os.environ.get(RUN_STATE_ENABLED_ENV)
    if raw is None:
        return True
    return raw.strip().lower() not in _DISABLED_VALUES


def save_run_state(
    request_id: str,
    *,
    message: str,
    has_image: bool,
    mode: str,
    decision: dict[str, Any],
    plan: ExecutionPlan,
    step_results: list[StepResult],
    run_status: Optional[str] = None,
) -> None:
    """Écrit le checkpoint. Non-bloquant : tout échec est avalé."""
    if not is_run_state_enabled():
        return
    try:
        snapshot = {
            "state_version": STATE_VERSION,
            "request_id": request_id,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "message": message,
            "has_image": has_image,
            "mode": mode,
            "run_status": run_status,
            "decision": decision,
            "plan": {
                "task_type": plan.task_type,
                "strategy": plan.strategy,
                "steps": [asdict(step) for step in plan.steps],
            },
            "step_results": [asdict(result) for result in step_results],
        }
        # Contrat canonique des ids (run_identity) : un id invalide ne doit
        # jamais nommer un chemin — échec avalé comme toute autre IO.
        run_dir = resolve_run_dir(get_run_events_dir(), request_id)
        if run_dir is None:
            raise ValueError(f"invalid request_id: {request_id!r}")
        run_dir.mkdir(parents=True, exist_ok=True)
        # Écriture ATOMIQUE : le checkpoint protège contre les interruptions,
        # il ne peut pas être lui-même corruptible par une interruption. On
        # écrit dans un temporaire du MÊME dossier (os.replace exige le même
        # système de fichiers), flush + fsync (le rename ne garantit pas le
        # contenu), puis remplacement atomique — un crash laisse soit l'ancien
        # state.json intact, soit le nouveau complet, jamais un JSON tronqué.
        final_path = run_dir / STATE_FILENAME
        tmp_path = run_dir / f"{STATE_FILENAME}.{uuid4().hex}.tmp"
        try:
            with tmp_path.open("w", encoding="utf-8") as f:
                f.write(json.dumps(snapshot, ensure_ascii=False, default=str))
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, final_path)
        finally:
            tmp_path.unlink(missing_ok=True)  # no-op après un replace réussi
    except Exception as exc:  # noqa: BLE001 — invariant non-bloquant
        try:
            sys.stderr.write(f"[run_state] swallow {type(exc).__name__}: {exc}\n")
        except Exception:
            pass


def load_run_state(request_id: str) -> Optional[dict[str, Any]]:
    """Recharge le checkpoint. None si absent/corrompu/forme inattendue,
    ou si l'id viole le contrat canonique (jamais de résolution de chemin
    sur un id non validé — /resume expose ce chemin au client)."""
    run_dir = resolve_run_dir(get_run_events_dir(), request_id)
    if run_dir is None:
        return None
    path = run_dir / STATE_FILENAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("plan"), dict):
        return None
    if not data.get("message") or not isinstance(data.get("decision"), dict):
        return None
    return data


def _filtered_kwargs(cls, raw: dict[str, Any]) -> dict[str, Any]:
    """Ne garde que les champs connus du dataclass : un state.json écrit par
    une version plus récente (champs en plus) reste rechargeable."""
    fields = set(cls.__dataclass_fields__)
    return {k: v for k, v in raw.items() if k in fields}


def rebuild_plan(saved_plan: dict[str, Any]) -> ExecutionPlan:
    steps = [
        PlanStep(**_filtered_kwargs(PlanStep, raw))
        for raw in saved_plan.get("steps") or []
        if isinstance(raw, dict)
    ]
    return ExecutionPlan(
        task_type=saved_plan.get("task_type") or "",
        strategy=saved_plan.get("strategy") or "single_step",
        steps=steps,
    )


def rebuild_step_result(raw: dict[str, Any]) -> StepResult:
    return StepResult(**_filtered_kwargs(StepResult, raw))
