"""
run_locks.py — verrou d'exécution par request_id (mono-process).

Deux reprises simultanées du même run (double POST /resume, double clic
sur « Approve & continue » dans la Console, reprise déclenchée pendant que
le run tourne encore) exécuteraient deux fois les mêmes steps — deux
images payées, deux scripts Blender — et écriraient en même temps dans
events.jsonl et state.json. Ce module garantit qu'UN SEUL
execute/resume par request_id court à la fois dans ce process :

- registre en mémoire (set) protégé par un threading.Lock — les handlers
  FastAPI synchrones et les BackgroundTasks de la Console tournent dans
  des threads du même process ;
- acquisition NON-bloquante : un run déjà actif → RunBusyError, que l'API
  traduit en 409 et la Console en ré-abonnement au flux d'événements du
  run en cours (jamais de double exécution silencieuse, jamais d'attente).

LIMITE ASSUMÉE : la protection est par-process. Plusieurs workers uvicorn
ou plusieurs process ne partagent pas ce registre — le déploiement
canonique (backend local mono-process, voir ARCHITECTURE.md) est couvert ;
une file de jobs durable multi-worker reste explicitement hors périmètre.
Pas de Redis ni de verrou fichier : complexité disproportionnée pour le
produit actuel.

Stdlib uniquement.
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Iterator


class RunBusyError(RuntimeError):
    """Le run est déjà en cours d'exécution dans ce process."""

    def __init__(self, request_id: str):
        super().__init__(f"run {request_id} is already executing")
        self.request_id = request_id


_registry_lock = threading.Lock()
_active_runs: set[str] = set()


def is_run_active(request_id: str) -> bool:
    """Le run est-il en cours d'exécution dans ce process ?"""
    with _registry_lock:
        return request_id in _active_runs


@contextmanager
def run_execution_lock(request_id: str) -> Iterator[None]:
    """Réserve `request_id` pour la durée du bloc.

    RunBusyError immédiate (pas d'attente) si le run est déjà actif ; le
    verrou est toujours rendu, même si le bloc lève.
    """
    with _registry_lock:
        if request_id in _active_runs:
            raise RunBusyError(request_id)
        _active_runs.add(request_id)
    try:
        yield
    finally:
        with _registry_lock:
            _active_runs.discard(request_id)
