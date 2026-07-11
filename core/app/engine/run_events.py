"""
Journal d'événements de run unifié (observabilité, phase A).

Capture passive et non-bloquante du cycle de vie d'une requête
(`execute_request`) : décision de routage, plan construit, transitions
de steps, fin de run. Append-only JSONL, un fichier par run :

    outputs/runs/<request_id>/events.jsonl

Pourquoi : avant ce module, le decision_trace n'existait que dans la
réponse HTTP et sur stdout, et les manifests ne couvrent que la pipeline
Blender. Un run qui dégrade hors Blender ne laissait aucune trace
persistée. Ce journal est aussi le socle des chantiers suivants :
manifest repro, recherche/streaming dans la Console, reprise sur
checkpoint (le répertoire outputs/runs/<request_id>/ est prévu pour
accueillir state.json et un manifest de run).

Les événements restent LÉGERS : pas de sorties LLM complètes, pas de
stdout/stderr d'outils (déjà présents dans la réponse API et les
manifests). Un événement = une transition + méta courte.

Configuration (env vars) :
- AAC_RUN_EVENTS_ENABLED : "0" / "false" / "no" / "off" pour désactiver.
  Tout autre valeur (ou absence) = activé.
- AAC_RUN_EVENTS_DIR : répertoire racine de sortie. Défaut "outputs/runs",
  ou <parent de BLENDER_OUTPUT_DIR>/runs quand cette variable existe
  (conteneur à rootfs read-only : seul le volume /outputs est writable).
- AAC_RUN_EVENTS_RETENTION_DAYS : rétention en jours (même exigence C3
  que les trajectoires : les événements contiennent les prompts
  utilisateur). Défaut 30. "0" = purge désactivée.

Garanties (mêmes invariants que llm_trajectory_log) :
- Ne lève JAMAIS d'exception (IO error, permission, disque plein → swallow).
  Un événement perdu est acceptable ; un crash de la pipeline ne l'est pas.
- Aucun side-effect métier.
- Stdlib uniquement.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

from app.engine.run_identity import resolve_run_dir

RUN_EVENTS_ENABLED_ENV = "AAC_RUN_EVENTS_ENABLED"
RUN_EVENTS_DIR_ENV = "AAC_RUN_EVENTS_DIR"
DEFAULT_RUN_EVENTS_DIR = "outputs/runs"
RUN_EVENTS_RETENTION_DAYS_ENV = "AAC_RUN_EVENTS_RETENTION_DAYS"
DEFAULT_RUN_EVENTS_RETENTION_DAYS = 30
# Même base writable que le journal de trajectoires : en conteneur read_only,
# un chemin relatif viserait le rootfs en lecture seule (EROFS).
BLENDER_OUTPUT_DIR_ENV = "BLENDER_OUTPUT_DIR"

EVENTS_FILENAME = "events.jsonl"

_DISABLED_VALUES: frozenset[str] = frozenset({"0", "false", "no", "off"})


def is_run_events_enabled() -> bool:
    """Activation par défaut. Désactivable via env (CI, disque non writable...)."""
    raw = os.environ.get(RUN_EVENTS_ENABLED_ENV)
    if raw is None:
        return True
    return raw.strip().lower() not in _DISABLED_VALUES


def get_run_events_dir() -> Path:
    # 1) Override explicite (gagne toujours).
    raw = os.environ.get(RUN_EVENTS_DIR_ENV)
    if raw is not None and raw.strip() != "":
        return Path(raw.strip())
    # 2) Défaut conteneur : frère de BLENDER_OUTPUT_DIR (/outputs/blender → /outputs/runs).
    blender_out = os.environ.get(BLENDER_OUTPUT_DIR_ENV)
    if blender_out is not None and blender_out.strip() != "":
        return Path(blender_out.strip()).parent / "runs"
    # 3) Repli natif : chemin relatif writable.
    return Path(DEFAULT_RUN_EVENTS_DIR)


def get_run_events_retention_days() -> int:
    """Rétention en jours (≥ 0). 0 = purge désactivée. Valeur invalide → défaut."""
    raw = os.environ.get(RUN_EVENTS_RETENTION_DAYS_ENV)
    if raw is None or raw.strip() == "":
        return DEFAULT_RUN_EVENTS_RETENTION_DAYS
    try:
        days = int(raw.strip())
    except ValueError:
        return DEFAULT_RUN_EVENTS_RETENTION_DAYS
    return max(days, 0)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _purge_old_runs(base_dir: Path, now: datetime) -> None:
    """
    Purge best-effort des runs plus vieux que la rétention.

    Contrairement aux trajectoires (fichiers nommés par date), les runs sont
    nommés par request_id : la date fait foi via le mtime de events.jsonl
    (= dernière écriture = fin du run). Une copie/restauration retarde la
    purge — acceptable pour du best-effort. Conservateur par construction :
    on ne supprime QUE events.jsonl, puis le répertoire seulement s'il est
    vide (un state.json ou manifest futur le préserve). Un répertoire sans
    events.jsonl n'est jamais touché. Ne lève jamais (appelée sous le try
    enveloppant de emit_run_event).
    """
    retention_days = get_run_events_retention_days()
    if retention_days <= 0:
        return
    cutoff = now - timedelta(days=retention_days)
    for run_dir in base_dir.iterdir():
        if not run_dir.is_dir():
            continue
        events_file = run_dir / EVENTS_FILENAME
        try:
            mtime = datetime.fromtimestamp(events_file.stat().st_mtime, tz=timezone.utc)
        except OSError:
            continue
        if mtime >= cutoff:
            continue
        events_file.unlink(missing_ok=True)
        try:
            run_dir.rmdir()
        except OSError:
            pass  # non vide (autres artefacts de run) → on le garde


def emit_run_event(
    *,
    request_id: str,
    kind: str,
    data: Optional[Mapping[str, Any]] = None,
) -> None:
    """
    Écrit une ligne JSONL décrivant une transition du run.

    Arguments
    ---------
    request_id : identifiant du run (executor), nomme le répertoire.
    kind       : type d'événement, namespacé par point ("run.started",
                 "route.decided", "plan.built", "step.started",
                 "step.blocked", "step.finished", "run.finished", ...).
    data       : méta courte de l'événement (dict JSON-sérialisable).

    Non-bloquant : tout échec est silencieusement avalé, avec un warning
    stderr best-effort pour le diagnostic en dev.
    """
    if not is_run_events_enabled():
        return

    try:
        now = _utc_now()
        record: dict[str, Any] = {
            "ts": now.isoformat(),
            "request_id": request_id,
            "kind": kind,
            "data": dict(data) if data is not None else None,
        }
        line = json.dumps(record, ensure_ascii=False, default=str)

        base_dir = get_run_events_dir()
        # Contrat canonique des ids (run_identity) : un id invalide ne doit
        # jamais nommer un chemin — échec avalé comme toute autre IO.
        run_dir = resolve_run_dir(base_dir, request_id)
        if run_dir is None:
            raise ValueError(f"invalid request_id: {request_id!r}")
        first_event = not run_dir.exists()
        run_dir.mkdir(parents=True, exist_ok=True)
        path = run_dir / EVENTS_FILENAME
        with path.open("a", encoding="utf-8") as f:
            f.write(line)
            f.write("\n")
        # Purge au premier événement du run seulement : évite de scanner
        # le répertoire racine à chaque transition.
        if first_event:
            _purge_old_runs(base_dir, now)
    except Exception as exc:  # noqa: BLE001 — invariant non-bloquant
        try:
            sys.stderr.write(f"[run_events] swallow {type(exc).__name__}: {exc}\n")
        except Exception:
            pass
