"""
artistic_intent.py — Artistic Intent Layer V0 (H.3).

Extraction heuristique pure depuis le prompt utilisateur.
Aucun appel LLM supplémentaire. Coût zéro, sans dépendance externe.

Produit :
- ArtisticIntent : modèle Pydantic minimal
- parse_artistic_intent(message) -> ArtisticIntent
- write_intent_json(intent, output_dir) -> str | None  (best-effort, non bloquant)
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Modèle
# ---------------------------------------------------------------------------

class ArtisticIntent(BaseModel):
    """Brief structuré extrait heuristiquement depuis le prompt utilisateur."""
    user_intent: str = ""
    medium: str = "unknown"             # "3d_scene" | "product_render" | "animation" | "unknown"
    style: list[str] = Field(default_factory=list)
    mood: list[str] = Field(default_factory=list)
    subject_main: str = "unknown"
    subject_secondary: list[str] = Field(default_factory=list)
    composition_camera: str = "unknown"
    composition_lighting: str = "unknown"
    workflow_target: str = "blender_scene_preview"
    confidence: float = 0.0


# ---------------------------------------------------------------------------
# Tables heuristiques
# ---------------------------------------------------------------------------

_MEDIUM_RULES: list[tuple[list[str], str]] = [
    (["animation", "anim", "keyframe", "frame", "cycle", "loop", "tourne", "pivote", "bouge"], "animation"),
    # H.4.4 — "studio" retiré : c'est un terme d'éclairage (voir _LIGHTING_RULES), pas un medium.
    # "commercial" retiré : trop ambigu ("espace commercial" → intérieur, pas packshot produit).
    # Note : "product" est un substring de "production" (edge case connu, non corrigé ici — rare).
    # H.5.4.1 — Vocabulaire produit élargi : noms d'objets typiquement product (flacon, fiole,
    # bouteille, pot cosmétique, parfum, cosmétique) et expressions composées sans
    # ambiguïté ("rendu packshot", "objet héros", "hero prop", "prop cinématographique",
    # "prévisualisation cinématographique"). "cinématographique" SEUL n'est PAS listé
    # ici (cf. test négatif "scène cinématographique sombre dans une rue").
    (
        [
            "product", "produit", "packshot", "rendu produit", "rendu packshot",
            "flacon", "fiole", "bouteille de parfum", "parfum", "cosmétique",
            "pot cosmétique", "objet héros", "hero prop",
            "prop cinématographique", "prop cinematographique",
            "prévisualisation cinématographique", "previsualisation cinematographique",
        ],
        "product_render",
    ),
    (["scène", "scene", "monde", "world", "décor", "environ", "intérieur", "extérieur",
      "laboratoire", "labo", "rue", "hangar", "île", "forêt", "ville", "salle", "chambre"], "3d_scene"),
]

_STYLE_RULES: list[tuple[list[str], str]] = [
    (["cinématique", "cinematic", "film", "cinéma"], "cinematic"),
    (["sci-fi", "science-fiction", "futuriste", "futurisme", "cyberpunk"], "sci-fi"),
    (["fantasy", "fantastique", "médiéval", "medieval"], "fantasy"),
    (["cartoon", "stylisé", "stylized", "low poly", "lowpoly", "toon"], "stylized"),
    (["réaliste", "realistic", "photoréaliste", "photorealistic"], "realistic"),
    (["dark", "sombre", "noir", "grimdark"], "dark"),
    (["minimaliste", "minimalist", "minimal", "épuré"], "minimalist"),
    (["spatial", "space", "vaisseau", "spaceship", "alien"], "space"),
]

_MOOD_RULES: list[tuple[list[str], str]] = [
    (["mystère", "mystery", "mystérieux", "mysterious"], "mystery"),
    (["tension", "tendu", "suspense"], "tension"),
    (["sombre", "dark", "noir", "sinistre", "lugubre"], "dark"),
    (["chaleureux", "warm", "chaud", "cozy", "douillet"], "warm"),
    (["froid", "cold", "glacé", "glaçant", "gelé"], "cold"),
    (["épique", "epic", "grandiose", "majestueux"], "epic"),
    (["tranquille", "peaceful", "calme", "serein", "doux"], "peaceful"),
    (["dramatique", "dramatic", "intense"], "dramatic"),
    (["abandonné", "abandoned", "désert", "post-apocalyptique"], "desolate"),
]

_SUBJECT_RULES: list[tuple[list[str], str]] = [
    (["sphère", "sphere", "ball", "boule"], "sphère"),
    (["cube", "dé", "box", "boîte"], "cube"),
    (["robot", "androïde", "android", "droid"], "robot"),
    (["vaisseau", "spaceship", "spacecraft", "fusée"], "vaisseau spatial"),
    (["arbre", "tree", "forêt", "forest"], "arbre"),
    (["île", "island", "flottante"], "île flottante"),
    (["laboratoire", "labo", "lab"], "laboratoire"),
    (["rue", "street", "ruelle", "alley"], "rue"),
    (["salle", "room", "chambre", "hall"], "salle"),
    (["personnage", "character", "figure", "humain", "human"], "personnage"),
    (["bouteille", "bottle", "flacon", "fiole", "parfum"], "bouteille"),
    (["hangar", "garage", "depot"], "hangar"),
    (["maquette", "mockup", "modèle"], "maquette"),
    # H.5.4.1 — Sujets produit supplémentaires (déclencheurs typiques product_render).
    (["pot cosmétique", "pot cosmetique", "pot de crème", "pot de creme", "jar"], "pot"),
    (["tube cosmétique", "tube cosmetique", "tube de"], "tube"),
    (["bloc produit", "bloc rectangulaire", "packaging rectangulaire"], "bloc"),
]

_CAMERA_RULES: list[tuple[list[str], str]] = [
    (["gros plan", "close-up", "closeup", "close up"], "close-up"),
    (["large", "wide", "grand angle", "wide angle"], "wide"),
    (["plongée", "top-down", "vue du dessus", "aerial"], "top-down"),
    (["contre-plongée", "low angle", "bas"], "low-angle"),
    (["perspective", "profonde", "depth"], "perspective"),
    (["profil", "side view"], "side"),
]

_LIGHTING_RULES: list[tuple[list[str], str]] = [
    (["néon", "neon", "néons", "neons"], "neon"),
    (["naturelle", "natural", "soleil", "sun", "jour", "daylight"], "natural"),
    (["studio", "softbox"], "studio"),
    (["bougie", "candle", "chandelle", "flamme", "torche"], "candlelight"),
    (["urgence", "emergency", "bleue", "blue", "lumière bleue", "blue light"], "blue emergency"),
    (["dramatique", "dramatic", "contre-jour", "backlight"], "dramatic"),
    (["douce", "soft", "diffuse"], "soft"),
    (["chaude", "warm light"], "warm"),
    (["sombre", "dim", "faible", "tamisé", "low light"], "dim"),
]


# ---------------------------------------------------------------------------
# Logique d'extraction
# ---------------------------------------------------------------------------

def _match_any(text_lower: str, keywords: list[str]) -> bool:
    return any(kw in text_lower for kw in keywords)


def _collect_matches(text_lower: str, rules: list[tuple[list[str], str]]) -> list[str]:
    """Retourne la liste de toutes les étiquettes dont au moins un mot-clé est trouvé."""
    results: list[str] = []
    for keywords, label in rules:
        if _match_any(text_lower, keywords) and label not in results:
            results.append(label)
    return results


def _first_match(text_lower: str, rules: list[tuple[list[str], str]], default: str = "unknown") -> str:
    for keywords, label in rules:
        if _match_any(text_lower, keywords):
            return label
    return default


def _build_user_intent(message: str) -> str:
    """Reformulation courte : première phrase tronquée à 120 caractères."""
    first_sentence = re.split(r"[.!?\n]", message.strip())[0].strip()
    return first_sentence[:120] if first_sentence else message.strip()[:120]


def _score_confidence(
    medium: str,
    style: list[str],
    mood: list[str],
    subject_main: str,
) -> float:
    """
    Score heuristique simple :
    - medium détecté (+0.25), style (+0.15 par tag, max 0.30), mood (+0.10 par tag, max 0.20),
    - subject non "unknown" (+0.25)
    Capped à 1.0.
    """
    score = 0.0
    if medium != "unknown":
        score += 0.25
    score += min(len(style) * 0.15, 0.30)
    score += min(len(mood) * 0.10, 0.20)
    if subject_main != "unknown":
        score += 0.25
    return round(min(score, 1.0), 2)


def parse_artistic_intent(message: str) -> ArtisticIntent:
    """
    Extrait heuristiquement les dimensions artistiques depuis le prompt utilisateur.
    Aucun appel LLM. Toujours retourne une ArtisticIntent valide.
    En cas d'erreur inattendue, retourne un intent vide safe.
    """
    if not message or not message.strip():
        return ArtisticIntent(
            user_intent="",
            medium="unknown",
            style=[],
            mood=[],
            subject_main="unknown",
            subject_secondary=[],
            composition_camera="unknown",
            composition_lighting="unknown",
            workflow_target="blender_scene_preview",
            confidence=0.0,
        )

    try:
        text_lower = message.lower()

        medium = _first_match(text_lower, _MEDIUM_RULES, default="3d_scene")
        style = _collect_matches(text_lower, _STYLE_RULES)
        mood = _collect_matches(text_lower, _MOOD_RULES)

        # Sujet principal : premier match
        subject_main = _first_match(text_lower, _SUBJECT_RULES, default="unknown")

        # Sujets secondaires : tous les autres matches
        all_subjects = _collect_matches(text_lower, _SUBJECT_RULES)
        subject_secondary = [s for s in all_subjects if s != subject_main]

        composition_camera = _first_match(text_lower, _CAMERA_RULES, default="unknown")
        composition_lighting = _first_match(text_lower, _LIGHTING_RULES, default="unknown")
        user_intent = _build_user_intent(message)
        confidence = _score_confidence(medium, style, mood, subject_main)

        return ArtisticIntent(
            user_intent=user_intent,
            medium=medium,
            style=style,
            mood=mood,
            subject_main=subject_main,
            subject_secondary=subject_secondary,
            composition_camera=composition_camera,
            composition_lighting=composition_lighting,
            workflow_target="blender_scene_preview",
            confidence=confidence,
        )

    except Exception as exc:  # noqa: BLE001
        print(f"[artistic_intent] parse failed (non-blocking): {exc}", file=sys.stderr)
        return ArtisticIntent()


# ---------------------------------------------------------------------------
# Écriture sur disque
# ---------------------------------------------------------------------------

def write_intent_json(intent: ArtisticIntent, output_dir: str) -> str | None:
    """
    Écrit intent.json dans output_dir.
    Retourne le chemin absolu si succès, None sinon.
    Jamais bloquant : toute exception est swallowed et loggée sur stderr.
    """
    try:
        path = Path(output_dir) / "intent.json"
        path.write_text(
            json.dumps(intent.model_dump(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return str(path)
    except Exception as exc:  # noqa: BLE001
        print(f"[artistic_intent] write_intent_json failed (non-blocking): {exc}", file=sys.stderr)
        return None
