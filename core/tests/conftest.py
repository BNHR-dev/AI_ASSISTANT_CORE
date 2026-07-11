"""
H.6.1 — Conftest racine pour la suite de tests `core/tests`.

Désactive par défaut l'écriture du journal de trajectoires LLM
(`app.engine.llm_trajectory_log`) pendant les tests, afin qu'aucun test
ne crée par effet de bord un fichier sous `outputs/blender/_trajectories/`.
Même traitement pour le journal d'événements de run (`app.engine.run_events`)
qui écrirait sinon sous `outputs/runs/` à chaque execute_request testé.

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


# Tiers de tests (chantier durcissement n°7) :
#   unit        — (défaut, sans marker) hermétique, aucun binaire ni service.
#   integration — exige un Blender local réel ; s'auto-skip quand absent
#                 (les skipif par fichier restent la source de vérité, le
#                 marker sert à SÉLECTIONNER : `pytest -m integration`).
#   live        — exerce la vraie stack (Ollama/ComfyUI/GPU). Coûteux et
#                 non hermétique : ne tourne QUE si AAC_LIVE_TESTS=1
#                 (lanceur : scripts/linux/live-tests.sh).
LIVE_TESTS_ENV = "AAC_LIVE_TESTS"


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
    config.addinivalue_line(
        "markers",
        "integration: needs a real local Blender binary (self-skips when absent)",
    )
    config.addinivalue_line(
        "markers",
        "live: exercises the real running stack (Ollama/ComfyUI/GPU); "
        "gated behind AAC_LIVE_TESTS=1",
    )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Le tier live est opt-in explicite : sans AAC_LIVE_TESTS=1, chaque test
    `live` est skippé avec une raison actionnable (jamais exécuté par erreur
    en CI ou sur une machine sans stack)."""
    if os.environ.get(LIVE_TESTS_ENV) == "1":
        return
    skip_live = pytest.mark.skip(
        reason="live tier disabled — set AAC_LIVE_TESTS=1 "
        "(launcher: scripts/linux/live-tests.sh)"
    )
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)


@pytest.fixture(autouse=True)
def _disable_trajectory_logging_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AAC_TRAJECTORY_LOG_ENABLED", "false")


@pytest.fixture(autouse=True)
def _disable_run_events_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AAC_RUN_EVENTS_ENABLED", "false")


@pytest.fixture(autouse=True)
def _disable_run_state_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Même hermétisme que les events : pas de state.json sous outputs/runs/
    par effet de bord des tests d'executor."""
    monkeypatch.setenv("AAC_RUN_STATE_ENABLED", "false")


@pytest.fixture(autouse=True)
def _disable_router_embeddings_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La couche embeddings du routeur ferait un appel HTTP (Ollama) sur les
    prompts sans signal : hermétisme d'abord, les tests dédiés réactivent."""
    monkeypatch.setenv("AAC_ROUTER_EMBEDDINGS", "0")
