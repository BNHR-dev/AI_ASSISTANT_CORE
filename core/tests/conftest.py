"""
H.6.1 — Conftest racine pour la suite de tests `core/tests`.

Désactive par défaut l'écriture du journal de trajectoires LLM
(`app.engine.llm_trajectory_log`) pendant les tests, afin qu'aucun test
ne crée par effet de bord un fichier sous `outputs/blender/_trajectories/`.

Les tests qui veulent vérifier le journal lui-même réactivent localement
le module via `monkeypatch.delenv(...)` ou `monkeypatch.setenv(..., "1")`,
ce qui est correctement restauré en fin de test.

Aucune autre globalité n'est introduite par ce conftest.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _disable_trajectory_logging_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AAC_TRAJECTORY_LOG_ENABLED", "false")
