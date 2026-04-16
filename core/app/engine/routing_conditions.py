from __future__ import annotations

from copy import deepcopy
from typing import Any


SIGNALS_BY_LOCALE = {
    "fr": {
        "code_request": [
            "exemple de code",
            "montre le code",
            "donne le code",
            "avec un script",
            "script python",
            "en python",
            "exemple python",
            "exemple en python",
            "python exemple",
            "un code simple",
            "un exemple en code",
        ],
        "improvement_request": [
            "améliore",
            "améliorer",
            "ameliore",
            "ameliorer",
            "version améliorée",
            "version amelioree",
            "réécris",
            "reecris",
            "réécrire",
            "reecrire",
            "propose une meilleure version",
            "meilleure version",
            "optimise",
            "optimiser",
            "refactor",
            "refactorise",
            "refactoriser",
        ],
        "implementation_request": [
            "implémentation",
            "implementation",
            "implémenter",
            "implementer",
            "prototype",
            "squelette",
            "code simple",
            "version simple",
            "script simple",
            "coder",
            "à coder",
            "code ça",
            "code moi",
        ],
        "example_request": [
            "exemple",
        ],
    },
    "en": {
        "code_request": [
            "code example",
            "show me the code",
            "give me the code",
            "with a script",
            "python script",
            "in python",
            "python example",
            "simple code",
            "code snippet",
            "write code",
        ],
        "improvement_request": [
            "improve",
            "improved version",
            "rewrite",
            "better version",
            "optimize",
            "optimise",
            "refactor",
            "make it better",
        ],
        "implementation_request": [
            "implementation",
            "implement",
            "prototype",
            "skeleton",
            "simple code",
            "simple version",
            "simple script",
            "to code",
            "code this",
            "code it",
            "implement it",
        ],
        "example_request": [
            "example",
        ],
    },
}


def normalize_text(text: str) -> str:
    return text.lower().strip()


def contains_any(text: str, signals: list[str]) -> bool:
    return any(signal in text for signal in signals)


def matches_signal(text: str, signal_key: str) -> bool:
    return any(
        contains_any(text, SIGNALS_BY_LOCALE[locale][signal_key])
        for locale in SIGNALS_BY_LOCALE
    )


def enrich_route_config(
    task_type: str,
    user_text: str,
    base_config: dict[str, Any],
) -> dict[str, Any]:
    text = normalize_text(user_text)
    enriched = deepcopy(base_config)

    enriched.setdefault("task_type", task_type)
    enriched.setdefault("second_call", None)
    enriched.setdefault("matched_rule", None)
    enriched.setdefault("reason_debug", None)

    wants_code = matches_signal(text, "code_request")
    wants_improvement = matches_signal(text, "improvement_request")
    wants_implementation = matches_signal(text, "implementation_request")
    wants_example = matches_signal(text, "example_request")
    mentions_python = "python" in text
    mentions_api = "api" in text

    if task_type == "explain_basic":
        if wants_code or (wants_example and (mentions_python or mentions_api)):
            enriched["second_call"] = "build"
            enriched["matched_rule"] = "explain_plus_code"
            enriched["reason_debug"] = "code_request_detected"
            return enriched

    if task_type == "critique" and wants_improvement:
        enriched["second_call"] = "build"
        enriched["matched_rule"] = "critique_plus_improvement"
        enriched["reason_debug"] = "improvement_request_detected"
        return enriched

    if task_type == "architecture":
        if wants_implementation or wants_code:
            enriched["second_call"] = "build"
            enriched["matched_rule"] = "architecture_plus_implementation"
            enriched["reason_debug"] = "implementation_request_detected"
            return enriched

    return enriched