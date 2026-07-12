"""
ollama_runtime.py — configuration BYO (« Bring Your Own ») de l'instance Ollama.

Chantier 6 : un utilisateur branche SON Ollama — local, distant, LAN ou
conteneurisé — sans toucher au code. Ce module est la SOURCE UNIQUE de la
configuration Ollama côté runtime : endpoint d'affichage, timeout, et
résolution des modèles par RÔLE. Les URLs d'API restent résolues par
runtime_urls (OLLAMA_BASE_URL / OLLAMA_GENERATE_URL / OLLAMA_TAGS_URL,
inchangées).

Rôles de modèles (env vars) — les défauts sont EXACTEMENT les valeurs
historiques de TASK_ROUTING : sans env posée, rien ne change.

- AAC_OLLAMA_GENERAL_MODEL : remplace "qwen3:8b" (explication, critique,
  synthèse, vision-texte…).
- AAC_OLLAMA_CODER_MODEL   : remplace "qwen2.5-coder:7b" (tâche build) ;
  sert aussi de repli à la pipeline Blender quand AAC_BLENDER_LLM_MODEL
  n'est pas posée (voir blender_model_config).
- AAC_OLLAMA_VISION_MODEL  : remplace "qwen2.5vl:3b" (requêtes avec image).
- AAC_EMBED_MODEL          : modèle d'embedding (défaut bge-m3) — consommé
  par router_embeddings, défini ICI pour n'exister qu'une fois.
- AAC_OLLAMA_TIMEOUT       : timeout (secondes) des appels /api/generate.
  Défaut 240 = la valeur historique du client.

Le remplacement se fait par NOM : `apply_model_override` mappe les noms
par défaut vers leur env de rôle, au point de sortie unique du routage
(`enrich_route_config` — chemins auto ET forcé). Un modèle inconnu du
mapping passe inchangé : pas d'abstraction multi-provider, pas de magie.

Stdlib uniquement (les sondes réseau vivent dans tool_manager et repro).
"""
from __future__ import annotations

import os
from typing import Optional

from app.infra.runtime_urls import get_ollama_generate_url

OLLAMA_TIMEOUT_ENV = "AAC_OLLAMA_TIMEOUT"
DEFAULT_OLLAMA_TIMEOUT = 240.0  # valeur historique d'ollama_client

GENERAL_MODEL_ENV = "AAC_OLLAMA_GENERAL_MODEL"
CODER_MODEL_ENV = "AAC_OLLAMA_CODER_MODEL"
VISION_MODEL_ENV = "AAC_OLLAMA_VISION_MODEL"
EMBED_MODEL_ENV = "AAC_EMBED_MODEL"
DEFAULT_EMBED_MODEL = "bge-m3"

# rôle → (nom par défaut dans TASK_ROUTING, env de remplacement)
MODEL_ROLES: dict[str, tuple[str, str]] = {
    "general": ("qwen3:8b", GENERAL_MODEL_ENV),
    "coder": ("qwen2.5-coder:7b", CODER_MODEL_ENV),
    "vision": ("qwen2.5vl:3b", VISION_MODEL_ENV),
}


def _env(name: str) -> Optional[str]:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return None
    return value.strip()


def get_ollama_base_url() -> str:
    """Racine de l'instance (pour l'affichage, /api/version, la provenance).

    Dérivée de l'URL generate résolue par runtime_urls : une seule logique
    de priorité d'env (OLLAMA_GENERATE_URL > OLLAMA_URL > OLLAMA_BASE_URL).
    """
    url = get_ollama_generate_url()
    suffix = "/api/generate"
    return url[: -len(suffix)] if url.endswith(suffix) else url


def get_ollama_timeout() -> float:
    """Timeout (s) des appels de génération. Invalide → défaut ; min 1 s."""
    raw = _env(OLLAMA_TIMEOUT_ENV)
    if raw is None:
        return DEFAULT_OLLAMA_TIMEOUT
    try:
        return max(1.0, float(raw))
    except ValueError:
        return DEFAULT_OLLAMA_TIMEOUT


def resolve_role_model(role: str) -> str:
    """Modèle effectif pour un rôle (`general` / `coder` / `vision`)."""
    default, env_name = MODEL_ROLES[role]
    return _env(env_name) or default


def apply_model_override(model: Optional[str]) -> Optional[str]:
    """Nom par défaut du routage → remplacement BYO ; sinon inchangé."""
    for default, env_name in MODEL_ROLES.values():
        if model == default:
            return _env(env_name) or model
    return model


def get_coder_model_override() -> Optional[str]:
    """Override du rôle coder, ou None — repli de la pipeline Blender."""
    return _env(CODER_MODEL_ENV)


def get_embed_model() -> str:
    """Modèle d'embedding (router_embeddings, futur RAG)."""
    return _env(EMBED_MODEL_ENV) or DEFAULT_EMBED_MODEL


def configured_generation_models() -> list[str]:
    """Modèles de GÉNÉRATION que la config actuelle peut invoquer : routage
    (post-override) + pipeline Blender. Uniques, ordre stable — c'est la
    liste que le health check exige de l'instance (le modèle d'embedding
    est volontairement à part : la couche embeddings du routeur se dégrade
    proprement quand il manque, il est optionnel).

    Imports tardifs : infra ne dépend d'engine qu'à l'exécution (même
    règle que les imports clients de tool_manager).
    """
    from app.engine.blender_model_config import get_blender_llm_model
    from app.engine.task_routing import TASK_ROUTING

    models: list[str] = []
    for route in TASK_ROUTING.values():
        resolved = apply_model_override(route.model)
        if resolved and resolved not in models:
            models.append(resolved)
    blender_model = get_blender_llm_model()
    if blender_model and blender_model not in models:
        models.append(blender_model)
    return models


def resolved_model_summary() -> dict[str, str]:
    """Vue par rôle des modèles effectifs (provenance, diagnostics)."""
    from app.engine.blender_model_config import get_blender_llm_model

    return {
        "general": resolve_role_model("general"),
        "coder": resolve_role_model("coder"),
        "vision": resolve_role_model("vision"),
        "blender": get_blender_llm_model(),
        "embedding": get_embed_model(),
    }
