"""
router_embeddings.py — Couche de rattrapage sémantique du routeur (chantier 3).

Le classifieur par mots-clés (task_classifier) est haute précision mais
aveugle aux formulations hors signaux : quand AUCUNE règle ne matche, il
retombe sur `explain_basic` sans regarder le sens. Cette couche prend ce
cas précis : elle encode la demande en *embedding* (vecteur sémantique,
via le modèle d'embedding servi par Ollama — déjà dans la stack) et la
classe par régression logistique.

Division du travail, volontairement conservatrice :
- les RÈGLES GAGNENT dès qu'elles matchent (score > 0) — cette couche ne
  les contredit jamais, elle ne voit que la zone morte ;
- l'entraînement est HORS LIGNE (scripts/train_router_classifier.py,
  scikit-learn côté dev) ; le runtime ne fait qu'un produit matriciel en
  pur Python sur les poids exportés en JSON — zéro dépendance nouvelle
  dans l'image ;
- dégradation garantie : modèle absent, Ollama injoignable, poids
  manquants ou entraînés pour un autre modèle d'embedding → None, et le
  comportement redevient EXACTEMENT celui d'aujourd'hui.

Config (env vars) :
- AAC_ROUTER_EMBEDDINGS      : "0"/"false"/... pour désactiver la couche.
- AAC_EMBED_MODEL            : modèle d'embedding Ollama (défaut bge-m3,
                               multilingue — le français est majoritaire).
- AAC_ROUTER_EMBED_MIN_PROB  : confiance minimale pour suivre la
                               prédiction (défaut 0.5) ; en dessous → None.
- AAC_ROUTER_WEIGHTS_PATH    : override du fichier de poids (tests).
- OLLAMA_BASE_URL            : base Ollama (défaut http://127.0.0.1:11434).
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any, Optional

import requests

# Source unique BYO Ollama (ollama_runtime) — ré-exportés ici pour les
# consommateurs historiques de ce module (tests, scripts d'entraînement).
from app.infra.ollama_runtime import (
    DEFAULT_EMBED_MODEL as DEFAULT_EMBED_MODEL,
    EMBED_MODEL_ENV as EMBED_MODEL_ENV,
    get_embed_model as get_embed_model,
)

ROUTER_EMBEDDINGS_ENABLED_ENV = "AAC_ROUTER_EMBEDDINGS"
MIN_PROB_ENV = "AAC_ROUTER_EMBED_MIN_PROB"
DEFAULT_MIN_PROB = 0.5
WEIGHTS_PATH_ENV = "AAC_ROUTER_WEIGHTS_PATH"
DEFAULT_WEIGHTS_PATH = Path(__file__).resolve().parent / "router_classifier_weights.json"

EMBED_TIMEOUT_SECONDS = 15  # chargement à froid du modèle d'embedding compris

_DISABLED_VALUES: frozenset[str] = frozenset({"0", "false", "no", "off"})

# Mémo des poids par chemin (relus une fois par process).
_weights_memo: dict[str, Optional[dict[str, Any]]] = {}


def is_embeddings_routing_enabled() -> bool:
    raw = os.environ.get(ROUTER_EMBEDDINGS_ENABLED_ENV)
    if raw is None:
        return True
    return raw.strip().lower() not in _DISABLED_VALUES


def get_min_prob() -> float:
    raw = os.environ.get(MIN_PROB_ENV)
    if raw is None or not raw.strip():
        return DEFAULT_MIN_PROB
    try:
        return min(1.0, max(0.0, float(raw.strip())))
    except ValueError:
        return DEFAULT_MIN_PROB


def _weights_path() -> Path:
    raw = os.environ.get(WEIGHTS_PATH_ENV)
    return Path(raw.strip()) if raw and raw.strip() else DEFAULT_WEIGHTS_PATH


def load_weights() -> Optional[dict[str, Any]]:
    """Poids du classifieur : {model, classes, coef, intercept, ...}.
    None si absents/invalides. Mémoïsé par chemin."""
    path = _weights_path()
    key = str(path)
    if key in _weights_memo:
        return _weights_memo[key]

    weights: Optional[dict[str, Any]] = None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        classes = data.get("classes")
        coef = data.get("coef")
        intercept = data.get("intercept")
        if (
            isinstance(classes, list) and classes
            and isinstance(coef, list) and len(coef) == len(classes)
            and isinstance(intercept, list) and len(intercept) == len(classes)
        ):
            weights = data
    except (OSError, ValueError):
        weights = None

    _weights_memo[key] = weights
    return weights


def reset_weights_memo() -> None:
    """Tests uniquement."""
    _weights_memo.clear()


def embed_text(text: str) -> Optional[list[float]]:
    """Embedding du texte via Ollama /api/embed. None si indisponible."""
    base = (os.environ.get("OLLAMA_BASE_URL") or "http://127.0.0.1:11434").rstrip("/")
    try:
        response = requests.post(
            f"{base}/api/embed",
            json={"model": get_embed_model(), "input": text},
            timeout=EMBED_TIMEOUT_SECONDS,
        )
        if not response.ok:
            return None
        embeddings = response.json().get("embeddings")
        if not isinstance(embeddings, list) or not embeddings:
            return None
        vector = embeddings[0]
        return vector if isinstance(vector, list) and vector else None
    except (requests.RequestException, ValueError):
        return None


def _l2_normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vector))
    if norm == 0:
        return vector
    return [x / norm for x in vector]


def _softmax(logits: list[float]) -> list[float]:
    peak = max(logits)
    exps = [math.exp(x - peak) for x in logits]
    total = sum(exps)
    return [x / total for x in exps]


def predict(vector: list[float], weights: dict[str, Any]) -> tuple[str, float]:
    """Régression logistique en pur Python : logits = coef·x + b, softmax.
    L'embedding est L2-normalisé (l'entraînement l'est aussi)."""
    x = _l2_normalize(vector)
    logits = [
        sum(w * xi for w, xi in zip(row, x)) + b
        for row, b in zip(weights["coef"], weights["intercept"])
    ]
    probs = _softmax(logits)
    best = max(range(len(probs)), key=probs.__getitem__)
    return weights["classes"][best], probs[best]


def classify_with_embeddings(normalized_text: str) -> Optional[tuple[str, float]]:
    """
    Prédiction (task, prob) pour un texte DÉJÀ normalisé (normalize_text du
    classifieur — garantit l'invariance casse/accents, et évite un import
    circulaire task_classifier ↔ ce module).

    None si : couche désactivée, poids absents, poids entraînés pour un
    AUTRE modèle d'embedding (les espaces vectoriels ne sont pas
    compatibles), Ollama/modèle indisponible, ou confiance < seuil.
    Ne lève jamais.
    """
    if not is_embeddings_routing_enabled() or not normalized_text.strip():
        return None
    weights = load_weights()
    if weights is None:
        return None
    if weights.get("model") and weights["model"] != get_embed_model():
        return None
    vector = embed_text(normalized_text)
    if vector is None or (weights.get("dim") and len(vector) != weights["dim"]):
        return None
    try:
        task, prob = predict(vector, weights)
    except (TypeError, ValueError, IndexError):
        return None
    if prob < get_min_prob():
        return None
    return task, prob
