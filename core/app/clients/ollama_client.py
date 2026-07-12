"""
Ollama HTTP client — wrapper minimal `requests` autour de `/api/generate`.

H.6.5.a — kwargs optionnels `options` et `format` ajoutés pour permettre
aux appelants de contrôler explicitement les paramètres d'inférence
(temperature, seed, top_p/top_k, num_ctx, ...) et le format de sortie
(`"json"` pour forcer un objet JSON syntaxiquement valide).

Rétrocompatibilité : les appels existants `generate_with_ollama(model, prompt)`
restent strictement identiques au comportement pré-H.6.5.a quand
`options=None` et `format=None`.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional

import requests

from app.infra.ollama_runtime import get_ollama_timeout
from app.infra.runtime_urls import get_ollama_generate_url


def generate_with_ollama(
    model: str,
    prompt: str,
    *,
    options: Optional[Mapping[str, Any]] = None,
    format: Optional[str] = None,
) -> str:
    """
    Appelle l'API Ollama `/api/generate` en mode non-streaming.

    Arguments
    ---------
    model   : nom du modèle Ollama (ex. "qwen2.5-coder:7b").
    prompt  : prompt complet à envoyer.
    options : dict optionnel des paramètres d'inférence Ollama
              (`temperature`, `seed`, `top_p`, `top_k`, `num_ctx`, etc.).
              Injecté tel quel dans la clé `options` du payload si non-None.
    format  : valeur optionnelle pour la clé `format` du payload Ollama.
              `"json"` force une sortie JSON syntaxiquement valide côté serveur.
              Injecté tel quel si non-None.

    Retourne le champ `response` du JSON Ollama, strippé. Lève RuntimeError
    si le serveur renvoie un code non-OK.
    """
    payload: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "stream": False,
    }
    if options is not None:
        # On passe un dict natif (pas un Mapping immuable) pour éviter une
        # sérialisation surprenante côté `requests`/`json`.
        payload["options"] = dict(options)
    if format is not None:
        payload["format"] = format

    url = get_ollama_generate_url()
    try:
        response = requests.post(url, json=payload, timeout=get_ollama_timeout())
    except requests.RequestException as exc:
        # BYO Ollama : l'erreur doit dire OÙ on a frappé et QUOI vérifier —
        # « connection refused » nu n'aide pas quelqu'un qui branche son
        # instance LAN/distante.
        raise RuntimeError(
            f"Ollama unreachable at {url} ({type(exc).__name__}: {exc}). "
            "Check that the server is running and that OLLAMA_BASE_URL points "
            "to it — see docs/OLLAMA.md."
        ) from exc

    if not response.ok:
        detail = f"Ollama error {response.status_code}: {response.text} (endpoint: {url})"
        if response.status_code == 404 and "not found" in response.text.lower():
            detail += (
                f" — the model {model!r} is probably absent from this instance: "
                f"try `ollama pull {model}`."
            )
        raise RuntimeError(detail)

    data = response.json()
    return data.get("response", "").strip()
