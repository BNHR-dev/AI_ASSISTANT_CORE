"""
Tests de la couche embeddings du routeur (app.engine.router_embeddings)
et de son intégration hybride dans task_classifier (chantier 3).

Invariants couverts :
- Les RÈGLES GAGNENT : dès qu'un signal matche, la couche embeddings
  n'est jamais consultée.
- La couche ne sert QUE la zone morte (aucun signal) et seulement si la
  confiance dépasse le seuil ; sinon comportement historique
  (explain_basic) à l'identique.
- Dégradation garantie : désactivée / poids absents / poids d'un autre
  modèle d'embedding / Ollama injoignable / dimension inattendue → None,
  jamais d'exception.
- Inférence pure Python : normalisation L2 + softmax corrects sur des
  poids artisanaux dont la prédiction se calcule à la main.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.engine import router_embeddings as remb
from app.task_classifier import classify_task


@pytest.fixture(autouse=True)
def _fresh_memo():
    remb.reset_weights_memo()
    yield
    remb.reset_weights_memo()


def _write_weights(path: Path, *, model: str = "bge-m3", dim: int = 4) -> None:
    """Poids artisanaux : la classe prédite est lisible sur le vecteur.
    coef aligné sur l'axe 0 → image_generation, axe 1 → build, axe 2 → quiz."""
    path.write_text(json.dumps({
        "model": model,
        "dim": dim,
        "classes": ["image_generation", "build", "quiz"],
        "coef": [[8, 0, 0, 0], [0, 8, 0, 0], [0, 0, 8, 0]],
        "intercept": [0.0, 0.0, 0.0],
    }), encoding="utf-8")


def _enable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, vector=None,
            model: str = "bge-m3") -> dict:
    """Active la couche avec poids artisanaux + embed mocké. Retourne un
    témoin des appels d'embedding."""
    weights = tmp_path / "weights.json"
    _write_weights(weights, model=model)
    monkeypatch.setenv("AAC_ROUTER_EMBEDDINGS", "1")
    monkeypatch.setenv("AAC_ROUTER_WEIGHTS_PATH", str(weights))
    monkeypatch.delenv("AAC_EMBED_MODEL", raising=False)
    calls: dict = {"texts": []}

    def fake_embed(text: str):
        calls["texts"].append(text)
        return vector

    monkeypatch.setattr(remb, "embed_text", fake_embed)
    return calls


# ---------------------------------------------------------------------------
# Inférence pure
# ---------------------------------------------------------------------------

def test_predict_math_is_correct() -> None:
    weights = {
        "classes": ["a", "b"],
        "coef": [[2.0, 0.0], [0.0, 2.0]],
        "intercept": [0.0, 0.0],
    }
    task, prob = remb.predict([10.0, 0.0], weights)  # L2-normalisé → [1, 0]
    assert task == "a"
    # logits = [2, 0] → softmax(2) = e²/(e²+1) ≈ 0.8808
    assert prob == pytest.approx(0.8808, abs=1e-3)


# ---------------------------------------------------------------------------
# classify_with_embeddings — contrats de dégradation
# ---------------------------------------------------------------------------

def test_disabled_returns_none(monkeypatch, tmp_path) -> None:
    _enable(monkeypatch, tmp_path, vector=[1, 0, 0, 0])
    monkeypatch.setenv("AAC_ROUTER_EMBEDDINGS", "0")
    assert remb.classify_with_embeddings("un visuel de renard") is None


def test_missing_weights_short_circuits_before_embedding(monkeypatch, tmp_path) -> None:
    calls = _enable(monkeypatch, tmp_path, vector=[1, 0, 0, 0])
    monkeypatch.setenv("AAC_ROUTER_WEIGHTS_PATH", str(tmp_path / "absent.json"))
    assert remb.classify_with_embeddings("un visuel de renard") is None
    assert calls["texts"] == []  # pas d'appel HTTP si pas de poids


def test_weights_for_another_model_are_refused(monkeypatch, tmp_path) -> None:
    _enable(monkeypatch, tmp_path, vector=[1, 0, 0, 0], model="nomic-embed-text")
    # runtime configuré sur bge-m3 (défaut) ≠ poids nomic → espaces incompatibles.
    assert remb.classify_with_embeddings("un visuel de renard") is None


def test_embedding_unavailable_returns_none(monkeypatch, tmp_path) -> None:
    _enable(monkeypatch, tmp_path, vector=None)  # Ollama down / modèle absent
    assert remb.classify_with_embeddings("un visuel de renard") is None


def test_dimension_mismatch_returns_none(monkeypatch, tmp_path) -> None:
    _enable(monkeypatch, tmp_path, vector=[1.0, 0.0])  # dim 2 ≠ dim 4 des poids
    assert remb.classify_with_embeddings("un visuel de renard") is None


def test_low_confidence_returns_none(monkeypatch, tmp_path) -> None:
    # Vecteur diagonal : probabilités quasi uniformes → sous le seuil.
    _enable(monkeypatch, tmp_path, vector=[1.0, 1.0, 1.0, 0.0])
    monkeypatch.setenv("AAC_ROUTER_EMBED_MIN_PROB", "0.9")
    assert remb.classify_with_embeddings("un visuel de renard") is None


def test_confident_prediction_returned(monkeypatch, tmp_path) -> None:
    _enable(monkeypatch, tmp_path, vector=[1.0, 0.0, 0.0, 0.0])
    result = remb.classify_with_embeddings("un visuel de renard")
    assert result is not None
    task, prob = result
    assert task == "image_generation"
    assert prob > 0.9


# ---------------------------------------------------------------------------
# Intégration hybride dans classify_task
# ---------------------------------------------------------------------------

def test_rules_win_and_embeddings_never_consulted(monkeypatch, tmp_path) -> None:
    calls = _enable(monkeypatch, tmp_path, vector=[0.0, 1.0, 0.0, 0.0])
    # « genere une image » est un signal fort : les règles routent, point.
    task, reason = classify_task("génère une image d'un chat")
    assert task == "image_generation"
    assert "embedding_fallback" not in reason
    assert calls["texts"] == []


def test_dead_zone_routed_by_embeddings(monkeypatch, tmp_path) -> None:
    calls = _enable(monkeypatch, tmp_path, vector=[1.0, 0.0, 0.0, 0.0])
    # Aucune règle ne matche cette formulation → couche embeddings.
    task, reason = classify_task("j'aimerais un joli visuel de renard roux")
    assert task == "image_generation"
    assert "embedding_fallback" in reason and "prob=" in reason
    # Le texte embeddé est le texte NORMALISÉ (invariance casse/accents).
    assert calls["texts"] == ["j aimerais un joli visuel de renard roux"]


def test_dead_zone_falls_back_to_legacy_when_layer_unavailable(monkeypatch, tmp_path) -> None:
    _enable(monkeypatch, tmp_path, vector=None)  # embeddings indisponibles
    task, reason = classify_task("j'aimerais un joli visuel de renard roux")
    assert task == "explain_basic"  # comportement historique inchangé
    assert "embedding_fallback" not in reason


def test_dead_zone_case_invariant_via_normalization(monkeypatch, tmp_path) -> None:
    calls = _enable(monkeypatch, tmp_path, vector=[1.0, 0.0, 0.0, 0.0])
    classify_task("J'AIMERAIS UN JOLI VISUEL DE RENARD ROUX")
    classify_task("j'aimerais un joli visuel de renard roux")
    assert calls["texts"][0] == calls["texts"][1]


def test_stale_class_in_weights_is_ignored(monkeypatch, tmp_path) -> None:
    weights = tmp_path / "weights.json"
    weights.write_text(json.dumps({
        "model": "bge-m3", "dim": 2,
        "classes": ["task_disparue"],
        "coef": [[8, 0]], "intercept": [0.0],
    }), encoding="utf-8")
    monkeypatch.setenv("AAC_ROUTER_EMBEDDINGS", "1")
    monkeypatch.setenv("AAC_ROUTER_WEIGHTS_PATH", str(weights))
    monkeypatch.setattr(remb, "embed_text", lambda text: [1.0, 0.0])

    task, reason = classify_task("j'aimerais un joli visuel de renard roux")
    assert task == "explain_basic"  # classe inconnue → filet historique


# ---------------------------------------------------------------------------
# Poids embarqués — cohérence du fichier versionné
# ---------------------------------------------------------------------------

def test_shipped_weights_are_valid_and_match_default_model() -> None:
    weights = remb.load_weights()
    assert weights is not None, "router_classifier_weights.json absent ou invalide"
    assert weights["model"] == remb.DEFAULT_EMBED_MODEL
    from app.task_classifier import TASKS

    assert set(weights["classes"]) <= set(TASKS)
    assert all(len(row) == weights["dim"] for row in weights["coef"])
    assert weights["metrics"]["cv_accuracy_mean"] >= 0.8  # gate de qualité
