from __future__ import annotations

import re
import unicodedata


TASKS = [
    "vision",
    "image_generation",
    "quiz",
    "critique",
    "web_research",
    "architecture",
    "explain_advanced",
    "explain_basic",
    "build",
]

PRIORITY = [
    "vision",
    "image_generation",
    "critique",
    "web_research",
    "architecture",
    "quiz",
    "explain_advanced",
    "explain_basic",
    "build",
]

# NOTE:
# - "vision" is handled explicitly via has_image=True and is not scored from text.
# - weights stay intentionally simple: small, visible, testable rules.
TASK_WEIGHTS = {
    "image_generation": 6,
    "explain_basic": 3,
    "explain_advanced": 3,
    "build": 2,
    "critique": 3,
    "quiz": 3,
    "web_research": 3,
    "architecture": 3,
}

TASK_REASON_CODES = {
    "image_generation": "visual_generation_request",
    "explain_basic": "basic_explanation_request",
    "explain_advanced": "advanced_explanation_request",
    "build": "build_request",
    "critique": "critique_request",
    "quiz": "quiz_request",
    "web_research": "web_research_request",
    "architecture": "architecture_request",
}

# Signals are stored in mostly normalized / accentless form because
# normalize_text() strips accents and punctuation.
SIGNALS_BY_LOCALE = {
    "fr": {
        "image_generation": [
            "genere une image",
            "generer une image",
            "cree une image",
            "creer une image",
            "fais moi une image",
            "fais moi un visuel",
            "cree un visuel",
            "creer un visuel",
            "fais une affiche",
            "cree une affiche",
            "image cyberpunk",
            "image de",
            "illustration de",
            "je veux une image de",
            "packshot",
            "rendu produit",
            "visuel pub",
            "visuel marketing",
            "portrait cinematique",
            "scene cinematique",
            "concept art",
            "key visual",
            "portrait cyberpunk",
            "variantes d un portrait",
            "variante d un portrait",
            "propositions d affiche",
            "proposition d affiche",
            "variantes d affiche",
            "variante d affiche",
            "2 propositions d affiche",
            "4 variantes d un portrait",
            "4 variantes",
            "2 propositions",
        ],
        "explain_advanced": [
            "detail",
            "mecanisme",
            "fonctionnement interne",
            "implications",
            "approfondis",
            "va plus loin",
            "en profondeur",
        ],
        "web_research": [
            "cherche",
            "recherche",
            "trouve",
            "news",
            "sources",
            "articles recents",
            "sur internet",
            "en ligne",
        ],
        "architecture": [
            "architecture",
            "architectures",
            "archi",
            "archis",
            "memoire",
            "pipeline",
            "routing",
            "router",
            "orchestrateur",
            "compare",
            "comparaison",
            "stocker",
            "stockage",
            "structure",
            "structurer",
            "comment gerer",
            "tu me proposes quoi",
            "quoi choisir",
            "choisir entre",
            "je sais pas quoi choisir",
            "j hesite",
        ],
        "critique": [
            "corrige",
            "critique",
            "correction",
            "erreur",
            "erreurs",
            "bug",
            "bugs",
            "feedback",
            "review",
            "relis",
            "ameliore",
        ],
        "quiz": [
            "quiz",
            "teste moi",
            "interroge moi",
            "pose moi des questions",
        ],
        "explain_basic": [
            "explique",
            "c est quoi",
            "cest quoi",
            "definition",
            "simplement",
            "je comprends rien",
            "je comprends rien a",
        ],
        "build": [
            "python",
            "code",
            "script",
            "fonction",
            "api",
            "json",
            "module",
            "classe",
            "class",
            "parser",
            "regex",
            "sql",
            "fastapi",
            "ecris du code",
            "implemente",
        ],
    },
    "en": {
        "image_generation": [
            "generate an image",
            "create an image",
            "make an image",
            "make me an image",
            "create a visual",
            "make a visual",
            "image of",
            "cover art",
            "key visual",
            "packshot",
            "product render",
            "product shot",
            "cinematic portrait",
            "cinematic scene",
            "concept art",
        ],
        "explain_advanced": [
            "detailed",
            "detail",
            "mechanism",
            "how it works",
            "internal workings",
            "implications",
            "go deeper",
            "in depth",
            "advanced",
        ],
        "web_research": [
            "search",
            "look up",
            "find",
            "news",
            "sources",
            "recent articles",
            "on the internet",
            "online",
            "latest",
        ],
        "architecture": [
            "architecture",
            "architectures",
            "memory",
            "pipeline",
            "routing",
            "router",
            "orchestrator",
            "compare",
            "comparison",
            "storage",
            "structure",
            "how should i design",
            "what should i choose",
            "choose between",
            "i dont know what to choose",
            "i hesitate",
            "im hesitating",
            "i m hesitating",
        ],
        "critique": [
            "correct",
            "critique",
            "correction",
            "error",
            "errors",
            "bug",
            "bugs",
            "feedback",
            "review",
            "improve",
            "fix",
        ],
        "quiz": [
            "quiz",
            "test me",
            "ask me questions",
            "question me",
        ],
        "explain_basic": [
            "explain",
            "what is",
            "definition",
            "simply",
            "i dont understand",
        ],
        "build": [
            "python",
            "code",
            "script",
            "function",
            "api",
            "json",
            "module",
            "class",
            "parser",
            "regex",
            "sql",
            "fastapi",
            "write code",
            "implement",
        ],
    },
}


def normalize_text(text: str) -> str:
    """
    Normalize text for simple substring matching:
    - lowercase
    - strip accents
    - replace punctuation / hyphens / apostrophes with spaces
    - collapse whitespace
    """
    text = text.lower().strip()
    text = text.replace("’", "'")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("'", " ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def contains_any(text: str, signals: list[str]) -> bool:
    return any(normalize_text(signal) in text for signal in signals)


def add_score(
    scores: dict[str, int],
    reasons: dict[str, list[str]],
    task: str,
    points: int,
    reason: str,
) -> None:
    scores[task] += points
    reasons[task].append(f"+{points} {reason}")


def penalize_score(
    scores: dict[str, int],
    reasons: dict[str, list[str]],
    task: str,
    points: int,
    reason: str,
) -> None:
    scores[task] = max(0, scores[task] - points)
    reasons[task].append(f"-{points} {reason}")


def contains_build_intent(text: str) -> bool:
    build_terms = [
        "python",
        "code",
        "script",
        "api",
        "json",
        "module",
        "classe",
        "class",
        "fonction",
        "function",
        "fastapi",
        "implemente",
        "write code",
    ]
    return any(term in text for term in build_terms)


def contains_architecture_intent(text: str) -> bool:
    architecture_terms = [
        "architecture",
        "pipeline",
        "routing",
        "router",
        "orchestrateur",
        "orchestrator",
        "structure",
        "storage",
        "memoire",
        "memory",
        "how should i design",
        "quoi choisir",
        "choose between",
    ]
    return any(term in text for term in architecture_terms)


def contains_explicit_visual_generation_intent(text: str) -> bool:
    """
    Strong direct user intent for image generation only.
    Keep this narrower than SIGNALS_BY_LOCALE to avoid false positives
    on requests like 'ecris un script python pour generer une image'.
    """
    strong_visual_terms = [
        "genere une image",
        "cree une image",
        "fais moi une image",
        "fais moi un visuel",
        "cree un visuel",
        "fais une affiche",
        "je veux une image de",
        "make me an image",
        "create an image",
        "make an image",
        "create a visual",
        "make a visual",
        "packshot",
        "concept art",
        "product render",
        "product shot",
        "cinematic portrait",
        "cinematic scene",
        "portrait cinematique",
        "scene cinematique",
        "portrait cyberpunk",
        "variantes d un portrait",
        "variante d un portrait",
        "propositions d affiche",
        "proposition d affiche",
        "variantes d affiche",
        "variante d affiche",
    ]
    return any(term in text for term in strong_visual_terms)


def best_task(scores: dict[str, int], priority: list[str]) -> str:
    max_score = max(scores.values())

    if max_score == 0:
        return "explain_basic"

    candidates = [task for task, score in scores.items() if score == max_score]

    for task in priority:
        if task in candidates:
            return task

    return "explain_basic"


def task_matches_locale(text: str, task: str, locale: str) -> bool:
    return contains_any(text, SIGNALS_BY_LOCALE[locale].get(task, []))


def count_locale_hits(text: str, locale: str) -> int:
    hits = 0
    for task in SIGNALS_BY_LOCALE[locale]:
        if task_matches_locale(text, task, locale):
            hits += 1
    return hits


def detect_signal_locale(text: str) -> tuple[str, str]:
    fr_hits = count_locale_hits(text, "fr")
    en_hits = count_locale_hits(text, "en")

    if fr_hits == 0 and en_hits == 0:
        return "unknown", "locale_detection:unknown"

    if fr_hits == en_hits and fr_hits > 0:
        return "mixed", f"locale_detection:mixed(fr={fr_hits},en={en_hits})"

    if fr_hits > en_hits:
        return "fr", f"locale_detection:fr(fr={fr_hits},en={en_hits})"

    return "en", f"locale_detection:en(fr={fr_hits},en={en_hits})"


def apply_signal_pack(
    text: str,
    locale: str,
    scores: dict[str, int],
    reasons: dict[str, list[str]],
) -> None:
    for task in SIGNALS_BY_LOCALE[locale]:
        if task_matches_locale(text, task, locale):
            add_score(
                scores,
                reasons,
                task,
                TASK_WEIGHTS[task],
                f"{locale}:{TASK_REASON_CODES[task]}",
            )


def apply_mixed_signal_pack(
    text: str,
    scores: dict[str, int],
    reasons: dict[str, list[str]],
) -> None:
    for task in TASK_WEIGHTS:
        matched_locales = [
            locale
            for locale in ("fr", "en")
            if task_matches_locale(text, task, locale)
        ]
        if matched_locales:
            add_score(
                scores,
                reasons,
                task,
                TASK_WEIGHTS[task],
                f"mixed:{TASK_REASON_CODES[task]}:{'+'.join(matched_locales)}",
            )


def apply_visual_guardrails(
    text: str,
    scores: dict[str, int],
    reasons: dict[str, list[str]],
) -> None:
    """
    Prevent image_generation from swallowing build / architecture requests
    unless the visual intent is clearly direct and primary.
    """
    if scores["image_generation"] <= 0:
        return

    explicit_visual = contains_explicit_visual_generation_intent(text)

    if contains_build_intent(text):
        penalize_score(
            scores,
            reasons,
            "image_generation",
            4 if explicit_visual else 6,
            "guardrail:build_over_visual",
        )
        add_score(
            scores,
            reasons,
            "build",
            3,
            "guardrail:build_over_visual",
        )

    if contains_architecture_intent(text):
        penalize_score(
            scores,
            reasons,
            "image_generation",
            4 if explicit_visual else 6,
            "guardrail:architecture_over_visual",
        )
        add_score(
            scores,
            reasons,
            "architecture",
            3,
            "guardrail:architecture_over_visual",
        )


def classify_task_v2(message: str, has_image: bool = False) -> tuple[str, str]:
    text = normalize_text(message)

    if has_image:
        return "vision", "image_input_detected"

    scores = {task: 0 for task in TASKS}
    reasons = {task: [] for task in TASKS}

    detected_locale, locale_reason = detect_signal_locale(text)

    if detected_locale == "fr":
        apply_signal_pack(text, "fr", scores, reasons)
        if max(scores.values()) == 0:
            apply_signal_pack(text, "en", scores, reasons)
    elif detected_locale == "en":
        apply_signal_pack(text, "en", scores, reasons)
        if max(scores.values()) == 0:
            apply_signal_pack(text, "fr", scores, reasons)
    elif detected_locale == "mixed":
        apply_mixed_signal_pack(text, scores, reasons)
    else:
        apply_mixed_signal_pack(text, scores, reasons)

    apply_visual_guardrails(text, scores, reasons)

    task = best_task(scores, PRIORITY)
    reason = (
        f"{locale_reason}; "
        f"scores={scores}; "
        f"reasons={reasons.get(task, [])}"
    )
    return task, reason


def classify_task(message: str, has_image: bool = False) -> tuple[str, str]:
    return classify_task_v2(message, has_image)