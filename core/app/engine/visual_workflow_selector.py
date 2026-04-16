from __future__ import annotations

import re
import unicodedata

from app.engine.visual_types import VisualIntentAnalysis


SUBJECT_KEYWORDS = {
    "portrait": {
        "strong": {
            "portrait",
            "headshot",
            "selfie",
            "visage",
            "face",
            "profil",
            "buste",
            "close up",
            "close-up",
            "gros plan",
        },
        "weak": {
            "personnage",
            "homme",
            "femme",
            "girl",
            "boy",
            "woman",
            "man",
        },
    },
    "product": {
        "strong": {
            "produit",
            "product",
            "packshot",
            "flacon",
            "bouteille",
            "parfum",
            "montre",
            "chaussure",
            "sac",
            "bijou",
            "bague",
            "casque",
            "smartphone",
            "laptop",
            "sneaker",
        },
        "weak": {
            "objet",
            "voiture",
            "car",
        },
    },
    "scene": {
        "strong": {
            "scene",
            "scène",
            "street",
            "rue",
            "city",
            "ville",
            "landscape",
            "paysage",
            "environment",
            "decor",
            "décor",
            "alley",
        },
        "weak": {
            "cinematic",
            "cinema",
            "movie",
            "film",
            "cyberpunk",
            "neon",
            "néon",
            "night",
            "nuit",
            "pluie",
            "rain",
            "ambiance",
            "sci-fi",
            "scifi",
            "science fiction",
            "futuriste",
            "futuristic",
            "synthwave",
        },
    },
}

RENDER_KEYWORDS = {
    "packshot": {"packshot", "studio product", "studio shot"},
    "key_visual": {"key visual", "hero visual", "campaign visual"},
    "cover": {"cover", "cover art", "album cover", "book cover"},
    "poster": {"affiche", "poster", "movie poster", "film poster"},
}

STYLE_FLAG_KEYWORDS = {
    "cyberpunk": {"cyberpunk"},
    "sci_fi": {"sci-fi", "scifi", "science fiction", "futuriste", "futuristic", "synthwave"},
    "neon": {"neon", "néon"},
    "rainy": {"rain", "pluie", "wet", "humide", "humid"},
    "luxury": {"luxe", "luxury", "premium"},
    "studio": {"studio", "packshot"},
    "cinematic": {"cinematic", "cinema", "movie", "film"},
}

WORKFLOW_BY_SUBJECT = {
    "portrait": "portrait_basic_v1",
    "product": "object_basic_v1",
    "scene": "cinematic_scene_v1",
}

SUBJECT_TIEBREAK = {
    "portrait": 3,
    "product": 2,
    "scene": 1,
}

RENDER_TIEBREAK = {
    "packshot": 4,
    "key_visual": 3,
    "cover": 2,
    "poster": 1,
    "standard": 0,
}


def _normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text.lower())
    normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    normalized = normalized.replace("’", "'")
    normalized = re.sub(r"[-_/]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _normalize_keywords(values: set[str]) -> set[str]:
    return {_normalize_text(value) for value in values}


def _contains_any(text: str, keywords: set[str]) -> bool:
    return any(keyword in text for keyword in keywords)


def _count_matches(text: str, keywords: set[str]) -> int:
    return sum(1 for keyword in keywords if keyword in text)


NORMALIZED_SUBJECT_KEYWORDS = {
    subject: {
        "strong": _normalize_keywords(groups["strong"]),
        "weak": _normalize_keywords(groups["weak"]),
    }
    for subject, groups in SUBJECT_KEYWORDS.items()
}

NORMALIZED_RENDER_KEYWORDS = {
    intent: _normalize_keywords(values)
    for intent, values in RENDER_KEYWORDS.items()
}

NORMALIZED_STYLE_FLAG_KEYWORDS = {
    flag: _normalize_keywords(values)
    for flag, values in STYLE_FLAG_KEYWORDS.items()
}


def _compute_subject_scores(text: str) -> dict[str, int]:
    scores: dict[str, int] = {}

    for subject, groups in NORMALIZED_SUBJECT_KEYWORDS.items():
        strong_score = _count_matches(text, groups["strong"]) * 2
        weak_score = _count_matches(text, groups["weak"])
        scores[subject] = strong_score + weak_score

    return scores


def _compute_render_scores(text: str) -> dict[str, int]:
    scores = {
        "packshot": _count_matches(text, NORMALIZED_RENDER_KEYWORDS["packshot"]),
        "key_visual": _count_matches(text, NORMALIZED_RENDER_KEYWORDS["key_visual"]),
        "cover": _count_matches(text, NORMALIZED_RENDER_KEYWORDS["cover"]),
        "poster": _count_matches(text, NORMALIZED_RENDER_KEYWORDS["poster"]),
        "standard": 0,
    }
    return scores


def _select_subject_type(subject_scores: dict[str, int]) -> tuple[str, bool]:
    subject_type, best_score = max(
        subject_scores.items(),
        key=lambda item: (item[1], SUBJECT_TIEBREAK[item[0]]),
    )
    if best_score <= 0:
        return "scene", True
    return subject_type, False


def _select_render_intent(render_scores: dict[str, int]) -> tuple[str, bool]:
    render_intent, best_score = max(
        render_scores.items(),
        key=lambda item: (item[1], RENDER_TIEBREAK[item[0]]),
    )
    if best_score <= 0:
        return "standard", True
    return render_intent, False


def _detect_style_flags(text: str) -> list[str]:
    ordered_flags = []
    for flag in ("cyberpunk", "sci_fi", "neon", "rainy", "luxury", "studio", "cinematic"):
        if _contains_any(text, NORMALIZED_STYLE_FLAG_KEYWORDS[flag]):
            ordered_flags.append(flag)
    return ordered_flags


def analyze_visual_intent(message: str) -> VisualIntentAnalysis:
    text = _normalize_text(message or "")

    if not text:
        text = "image conceptuelle"

    subject_scores = _compute_subject_scores(text)
    render_scores = _compute_render_scores(text)

    # Petit coup de pouce utile :
    # si le rendu demandé est explicitement "packshot", on force légèrement le sujet "product"
    # sans créer de logique cachée ailleurs.
    if render_scores["packshot"] > 0:
        subject_scores["product"] += 2

    subject_type, used_subject_fallback = _select_subject_type(subject_scores)
    render_intent, used_render_default = _select_render_intent(render_scores)
    style_flags = _detect_style_flags(text)

    workflow_id = WORKFLOW_BY_SUBJECT[subject_type]

    reason_parts = [
        f"subject={subject_type}",
        f"render={render_intent}",
        f"workflow={workflow_id}",
        f"subject_scores={subject_scores}",
        f"render_scores={render_scores}",
    ]

    if style_flags:
        reason_parts.append(f"style_flags={style_flags}")
    if used_subject_fallback:
        reason_parts.append("subject_fallback=scene")
    if used_render_default:
        reason_parts.append("render_default=standard")

    reason = "; ".join(reason_parts)

    return VisualIntentAnalysis(
        subject_type=subject_type,
        render_intent=render_intent,
        style_flags=style_flags,
        workflow_id=workflow_id,
        reason=reason,
        subject_scores=subject_scores,
        render_scores=render_scores,
    )


def select_visual_workflow(message: str) -> tuple[str, str]:
    analysis = analyze_visual_intent(message)
    return analysis.workflow_id, analysis.reason