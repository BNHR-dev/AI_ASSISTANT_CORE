from typing import Optional


IMAGE_TOOL_SIGNALS = [
    "image",
    "génère une image",
    "genere une image",
    "générer une image",
    "generer une image",
    "crée une image",
    "cree une image",
    "créer une image",
    "creer une image",
    "generate an image",
    "create an image",
    "make an image",
    "render",
    "comfyui",
]


def select_tool(message: str, task_type: str) -> Optional[str]:
    text = message.lower().strip()

    if task_type == "web_research":
        return "web"

    if task_type == "image_generation":
        return "comfyui"

    if task_type == "blender_script":
        return "blender"

    if any(signal in text for signal in IMAGE_TOOL_SIGNALS):
        return "comfyui"

    return None