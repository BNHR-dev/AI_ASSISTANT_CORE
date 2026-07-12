"""
H.6.1 — Configuration centralisée du modèle LLM de la pipeline Blender.

Source de vérité unique pour le nom du modèle Ollama utilisé par les briques
LLM-dépendantes de la pipeline Blender (génération script, extraction IR
product_render, routing TaskRoute "blender_script").

Avant H.6.1, la chaîne "qwen2.5-coder:7b" était répétée littéralement dans
plusieurs fichiers de production. Tout changement de modèle imposait une
modification multi-fichiers, ce qui rendait impossible toute évaluation
comparative propre (ex. qwen2.5:14b, deepseek-coder-v2).

Comportement :
- Variable d'environnement `AAC_BLENDER_LLM_MODEL` (lecture dynamique).
- Défaut `qwen2.5-coder:7b` — identique à la valeur historique pour ne
  pas modifier la sortie du système si l'env n'est pas positionné.
- Aucune validation du contenu : c'est la couche Ollama qui rejettera un
  nom de modèle inconnu, avec un message explicite. On ne veut pas
  dupliquer cette logique ici.

Aucun I/O, aucun import LLM. Module isolé, importable par toutes les couches
de la pipeline sans risque de cycle.
"""
from __future__ import annotations

import os


BLENDER_LLM_MODEL_ENV = "AAC_BLENDER_LLM_MODEL"
DEFAULT_BLENDER_LLM_MODEL = "qwen2.5-coder:7b"


def get_blender_llm_model() -> str:
    """
    Retourne le nom du modèle LLM Ollama pour la pipeline Blender.

    Ordre de résolution : AAC_BLENDER_LLM_MODEL (spécifique) >
    AAC_OLLAMA_CODER_MODEL (rôle BYO — le défaut Blender EST le modèle
    coder du routage, un utilisateur qui remplace l'un veut presque
    toujours remplacer l'autre) > défaut historique.

    Lecture dynamique de l'environnement à chaque appel : un changement
    d'env entre deux appels est pris en compte sans redémarrage. Les
    consommateurs qui ont besoin d'une valeur figée (ex. constantes
    d'import comme `DEFAULT_EXTRACTION_MODEL`) peuvent l'appeler une fois
    au chargement du module.
    """
    value = os.environ.get(BLENDER_LLM_MODEL_ENV)
    if value is None or value.strip() == "":
        from app.infra.ollama_runtime import get_coder_model_override

        return get_coder_model_override() or DEFAULT_BLENDER_LLM_MODEL
    return value.strip()
