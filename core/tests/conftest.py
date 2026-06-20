"""
H.6.1 — Conftest racine pour la suite de tests `core/tests`.

Désactive par défaut l'écriture du journal de trajectoires LLM
(`app.engine.llm_trajectory_log`) pendant les tests, afin qu'aucun test
ne crée par effet de bord un fichier sous `outputs/blender/_trajectories/`.

Les tests qui veulent vérifier le journal lui-même réactivent localement
le module via `monkeypatch.delenv(...)` ou `monkeypatch.setenv(..., "1")`,
ce qui est correctement restauré en fin de test.

C1c — désactive aussi par défaut le sandbox bwrap (`AAC_BLENDER_SANDBOX=off`)
pour que la suite reste hermétique : sans cela, le défaut runtime `auto`
envelopperait les subprocess Blender mockés dans bwrap et validerait les
`output_dir` de test (souvent hors `outputs/blender`) → comportement parasite.
Les tests dédiés au sandbox (`test_blender_sandbox*.py`) surchargent ce défaut
via `monkeypatch.setenv(...)` pour exercer les modes réels.
"""
from __future__ import annotations

import os

import pytest


def pytest_configure(config: pytest.Config) -> None:
    """Défaut session-scope : sandbox bwrap désactivé pendant les tests.

    Posé via `pytest_configure` (avant toute fixture, y compris les fixtures
    module/session-scoped qui rendent du vrai Blender) plutôt que via une
    fixture function-scoped, sinon le setup d'un fixture module-scoped
    s'exécuterait hors de la portée du patch et validerait des `output_dir`
    de test hors `outputs/blender`. `setdefault` respecte une valeur déjà
    fournie par l'environnement (CI dédiée sandbox), et les tests sandbox
    surchargent par test via `monkeypatch.setenv(...)`.
    """
    os.environ.setdefault("AAC_BLENDER_SANDBOX", "off")


@pytest.fixture(autouse=True)
def _disable_trajectory_logging_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AAC_TRAJECTORY_LOG_ENABLED", "false")
