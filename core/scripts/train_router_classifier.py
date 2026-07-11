"""
train_router_classifier.py — Entraînement HORS LIGNE du classifieur de routage.

Outil de dev, jamais embarqué dans l'image : il lit le corpus étiqueté,
encode chaque exemple via le modèle d'embedding servi par Ollama, entraîne
une régression logistique (scikit-learn), évalue en validation croisée,
puis ré-entraîne sur tout le corpus et exporte les poids en JSON — le
runtime (app/engine/router_embeddings.py) ne fait que les relire en pur
Python.

Prérequis : un venv avec scikit-learn, et Ollama joignable avec le modèle
d'embedding tiré (`ollama pull bge-m3`).

  OLLAMA_BASE_URL=http://<ip>:11434 python scripts/train_router_classifier.py

Sorties :
  app/engine/router_classifier_weights.json   (poids + métriques + provenance)
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import requests

CORE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CORE))

from app.task_classifier import TASKS, normalize_text  # noqa: E402

CORPUS_PATH = CORE / "scripts" / "router_corpus.jsonl"
WEIGHTS_PATH = CORE / "app" / "engine" / "router_classifier_weights.json"
EMBED_MODEL = (os.environ.get("AAC_EMBED_MODEL") or "bge-m3").strip()
OLLAMA_BASE = (os.environ.get("OLLAMA_BASE_URL") or "http://127.0.0.1:11434").rstrip("/")


def embed(text: str) -> list[float]:
    response = requests.post(
        f"{OLLAMA_BASE}/api/embed",
        json={"model": EMBED_MODEL, "input": text},
        timeout=60,
    )
    response.raise_for_status()
    return response.json()["embeddings"][0]


def main() -> None:
    import numpy as np
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import cross_val_score

    rows = [json.loads(line) for line in CORPUS_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    unknown = {r["task"] for r in rows} - set(TASKS)
    assert not unknown, f"tâches inconnues dans le corpus : {unknown}"
    print(f"corpus : {len(rows)} exemples — {dict(Counter(r['task'] for r in rows))}")

    print(f"embedding via {OLLAMA_BASE} ({EMBED_MODEL})…")
    # Même normalisation qu'au runtime : le classifieur voit normalize_text(message).
    X = np.array([embed(normalize_text(r["text"])) for r in rows], dtype=np.float64)
    X /= np.linalg.norm(X, axis=1, keepdims=True)
    y = [r["task"] for r in rows]

    clf = LogisticRegression(max_iter=2000, C=10.0)
    scores = cross_val_score(clf, X, y, cv=5)
    print(f"accuracy CV 5-fold : {scores.mean():.3f} ± {scores.std():.3f}  {[round(s, 3) for s in scores]}")

    clf.fit(X, y)
    weights = {
        "model": EMBED_MODEL,
        "dim": int(X.shape[1]),
        "classes": list(clf.classes_),
        "coef": [[round(float(v), 6) for v in row] for row in clf.coef_],
        "intercept": [round(float(v), 6) for v in clf.intercept_],
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "corpus": {"path": str(CORPUS_PATH.relative_to(CORE)), "n_examples": len(rows)},
        "metrics": {"cv_accuracy_mean": round(float(scores.mean()), 4),
                    "cv_accuracy_std": round(float(scores.std()), 4), "cv_folds": 5},
    }
    WEIGHTS_PATH.write_text(json.dumps(weights, ensure_ascii=False), encoding="utf-8")
    print(f"poids écrits : {WEIGHTS_PATH} ({WEIGHTS_PATH.stat().st_size // 1024} Ko)")


if __name__ == "__main__":
    main()
