"""
H.6.1 — Journal des trajectoires LLM de la pipeline Blender.

Capture passive et non-bloquante des appels LLM (extracteur IR, génération
script). Append-only JSONL, une ligne par appel. But unique : constituer un
corpus exploitable pour les phases suivantes (eval harness H.6.2, format
strict H.6.3, fine-tuning éventuel ultérieur).

Pourquoi maintenant : sans collecte précoce, aucun dataset n'existera quand
le besoin se fera sentir. Démarrer la collecte dès H.6.1 garantit qu'à
H.6.2 on dispose déjà de cas réels pour calibrer l'eval harness.

Configuration (env vars) :
- AAC_TRAJECTORY_LOG_ENABLED : "0" / "false" / "no" pour désactiver. Tout
  autre valeur (ou absence) = activé.
- AAC_TRAJECTORY_LOG_DIR     : répertoire de sortie. Défaut
  "outputs/blender/_trajectories".
- AAC_TRAJECTORY_RETENTION_DAYS : rétention en jours (C3, audit 2026-06-10).
  Les fichiers YYYY-MM-DD.jsonl plus vieux que N jours sont purgés
  (best-effort) à chaque écriture. Défaut 30. "0" = purge désactivée.

Garanties :
- Ne lève JAMAIS d'exception (IO error, permission, disque plein → swallow).
  Une trajectoire perdue est acceptable ; un crash de la pipeline ne l'est pas.
- Aucun side-effect métier : ne modifie pas l'IR, ne modifie pas le script.
- Format JSONL : une ligne = un appel = un dict JSON, append-only.
- Fichier nommé par date UTC `YYYY-MM-DD.jsonl` pour rotation triviale.

Aucun import Pydantic ni LLM. Stdlib uniquement.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Optional


TRAJECTORY_LOG_ENABLED_ENV = "AAC_TRAJECTORY_LOG_ENABLED"
TRAJECTORY_LOG_DIR_ENV = "AAC_TRAJECTORY_LOG_DIR"
DEFAULT_TRAJECTORY_LOG_DIR = "outputs/blender/_trajectories"
TRAJECTORY_RETENTION_DAYS_ENV = "AAC_TRAJECTORY_RETENTION_DAYS"
DEFAULT_TRAJECTORY_RETENTION_DAYS = 30
# Dossier de sortie Blender (writable — p.ex. le volume /outputs/blender en conteneur
# read_only). Sert de base au DÉFAUT du journal de trajectoires, pour ne pas viser un
# chemin relatif qui tomberait sur un rootfs en lecture seule (EROFS).
BLENDER_OUTPUT_DIR_ENV = "BLENDER_OUTPUT_DIR"

# Valeurs reconnues comme "désactivé". Casse-insensible.
_DISABLED_VALUES: frozenset[str] = frozenset({"0", "false", "no", "off"})


def is_trajectory_logging_enabled() -> bool:
    """
    Activation par défaut. Désactivable via env (utile pour CI rapide,
    contextes où le disque n'est pas writable, etc.).
    """
    raw = os.environ.get(TRAJECTORY_LOG_ENABLED_ENV)
    if raw is None:
        return True
    return raw.strip().lower() not in _DISABLED_VALUES


def get_trajectory_log_dir() -> Path:
    # 1) Override explicite (gagne toujours).
    raw = os.environ.get(TRAJECTORY_LOG_DIR_ENV)
    if raw is not None and raw.strip() != "":
        return Path(raw.strip())
    # 2) Défaut : sous le dossier de sortie Blender (writable — volume /outputs/blender
    #    en conteneur read_only), plutôt qu'un chemin RELATIF qui viserait le rootfs en
    #    lecture seule (EROFS sous le mode Y durci).
    blender_out = os.environ.get(BLENDER_OUTPUT_DIR_ENV)
    if blender_out is not None and blender_out.strip() != "":
        return Path(blender_out.strip()) / "_trajectories"
    # 3) Repli historique (chemin relatif, writable en natif).
    return Path(DEFAULT_TRAJECTORY_LOG_DIR)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _current_log_path(base_dir: Path, now: datetime) -> Path:
    return base_dir / f"{now.strftime('%Y-%m-%d')}.jsonl"


def get_trajectory_retention_days() -> int:
    """Rétention en jours (≥ 0). 0 = purge désactivée. Valeur invalide → défaut."""
    raw = os.environ.get(TRAJECTORY_RETENTION_DAYS_ENV)
    if raw is None or raw.strip() == "":
        return DEFAULT_TRAJECTORY_RETENTION_DAYS
    try:
        days = int(raw.strip())
    except ValueError:
        return DEFAULT_TRAJECTORY_RETENTION_DAYS
    return max(days, 0)


def _purge_old_trajectories(base_dir: Path, now: datetime) -> None:
    """
    C3 (audit 2026-06-10) — Purge best-effort des fichiers de trajectoires
    plus vieux que la rétention. Les trajectoires contiennent les prompts
    utilisateur complets : sans purge, elles s'accumulent indéfiniment.

    La date fait foi via le NOM du fichier (YYYY-MM-DD.jsonl), pas le mtime :
    déterministe, insensible aux copies/restaurations. Un nom non conforme
    est ignoré (jamais supprimé). Ne lève jamais (appelée sous le try
    enveloppant de log_trajectory).
    """
    retention_days = get_trajectory_retention_days()
    if retention_days <= 0:
        return
    cutoff = now.date() - timedelta(days=retention_days)
    for path in base_dir.glob("*.jsonl"):
        try:
            file_date = datetime.strptime(path.stem, "%Y-%m-%d").date()
        except ValueError:
            continue
        if file_date < cutoff:
            path.unlink(missing_ok=True)


def log_trajectory(
    *,
    stage: str,
    model: str,
    prompt: str,
    raw_response: Optional[str],
    parse_ok: Optional[bool] = None,
    ir: Optional[Mapping[str, Any]] = None,
    fallback: bool = False,
    error: Optional[str] = None,
    request_id: Optional[str] = None,
    extra: Optional[Mapping[str, Any]] = None,
) -> None:
    """
    Écrit une ligne JSONL décrivant un appel LLM de la pipeline Blender.

    Arguments
    ---------
    stage        : identifiant court du site d'appel ("extractor", "script_gen", ...).
    model        : nom du modèle Ollama réellement utilisé.
    prompt       : prompt complet envoyé au LLM.
    raw_response : sortie brute du LLM (None si l'appel a échoué avant retour).
    parse_ok     : True si la sortie a été parsée OK, False si parsing échoué,
                   None si non pertinent à ce stage.
    ir           : représentation finale (dict) si applicable.
    fallback     : True si un fallback déterministe a été déclenché.
    error        : message d'erreur explicatif (None si succès).
    request_id   : identifiant de requête côté pipeline (corrélation avec
                   le scene_report / manifest), None si non disponible.
    extra        : slot d'extension (autres champs non standard). Stocké tel quel.

    Non-bloquant : tout échec est silencieusement avalé. Un warning vers
    stderr est émis pour faciliter le diagnostic en dev, mais sans jamais
    propager.
    """
    if not is_trajectory_logging_enabled():
        return

    try:
        now = _utc_now()
        record: dict[str, Any] = {
            "ts": now.isoformat(),
            "stage": stage,
            "model": model,
            "prompt": prompt,
            "raw_response": raw_response,
            "parse_ok": parse_ok,
            "ir": dict(ir) if ir is not None else None,
            "fallback": bool(fallback),
            "error": error,
            "request_id": request_id,
            "extra": dict(extra) if extra is not None else None,
        }
        line = json.dumps(record, ensure_ascii=False, default=str)

        base_dir = get_trajectory_log_dir()
        base_dir.mkdir(parents=True, exist_ok=True)
        path = _current_log_path(base_dir, now)
        with path.open("a", encoding="utf-8") as f:
            f.write(line)
            f.write("\n")
        # C3 — rétention : purge best-effort des jours expirés.
        _purge_old_trajectories(base_dir, now)
    except Exception as exc:  # noqa: BLE001 — invariant non-bloquant
        # Diagnostic best-effort, sans relancer.
        try:
            sys.stderr.write(
                f"[llm_trajectory_log] swallow {type(exc).__name__}: {exc}\n"
            )
        except Exception:
            pass
