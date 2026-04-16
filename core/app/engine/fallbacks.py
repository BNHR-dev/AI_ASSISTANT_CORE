from __future__ import annotations


FALLBACK_LABELS = {
    "fr": {
        "comfyui_error": "ComfyUI n'a pas pu terminer la tâche. Détail: {error}",
        "comfyui_unreachable": "ComfyUI est inaccessible actuellement. Vérifie qu'il tourne et que son API répond.",
        "comfyui_timeout": "ComfyUI a dépassé le délai d'attente pendant la génération.",
        "comfyui_no_output": "ComfyUI a terminé sans produire de fichier image exploitable.",
        "web_error": "La recherche web n'a pas pu aboutir. Détail: {error}",
        "ollama_error": "Le moteur LLM local n'a pas pu répondre. Détail: {error}",
        "tool_unavailable": "Outil indisponible: {tool_name}. Détail: {error}",
    },
    "en": {
        "comfyui_error": "ComfyUI could not complete the task. Detail: {error}",
        "comfyui_unreachable": "ComfyUI is currently unreachable. Verify that it is running and that its API responds.",
        "comfyui_timeout": "ComfyUI exceeded the allowed generation timeout.",
        "comfyui_no_output": "ComfyUI finished without producing a usable image file.",
        "web_error": "Web search could not complete successfully. Detail: {error}",
        "ollama_error": "The local LLM engine could not respond. Detail: {error}",
        "tool_unavailable": "Tool unavailable: {tool_name}. Detail: {error}",
    },
}


def get_fallback_labels(locale: str = "fr") -> dict[str, str]:
    return FALLBACK_LABELS.get(locale, FALLBACK_LABELS["fr"])


def fallback_text_for_tool_error(
    tool_name: str,
    error: str,
    locale: str = "fr",
) -> str:
    labels = get_fallback_labels(locale)
    normalized_error = (error or "").lower()

    if tool_name == "comfyui":
        if any(fragment in normalized_error for fragment in ["unable to reach comfyui", "runtime unavailable", "api is exposed", "inaccessible"]):
            return labels["comfyui_unreachable"]

        if "timeout" in normalized_error:
            return labels["comfyui_timeout"]

        if any(fragment in normalized_error for fragment in ["no usable output", "no usable output file", "no usable output files", "aucun fichier"]):
            return labels["comfyui_no_output"]

        return labels["comfyui_error"].format(error=error)

    if tool_name == "web":
        return labels["web_error"].format(error=error)

    if tool_name == "ollama":
        return labels["ollama_error"].format(error=error)

    return labels["tool_unavailable"].format(tool_name=tool_name, error=error)


def fallback_text_for_step_error(
    step_type: str,
    tool_name: str | None,
    error: str,
    locale: str = "fr",
) -> str | None:
    if tool_name:
        return fallback_text_for_tool_error(tool_name, error, locale=locale)

    if step_type.startswith("llm_"):
        return fallback_text_for_tool_error("ollama", error, locale=locale)

    return None
