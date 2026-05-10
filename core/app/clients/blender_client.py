from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from app.clients.ollama_client import generate_with_ollama
from app.engine.blender_types import BlenderRequest, BlenderResult


BLENDER_EXE = os.getenv("BLENDER_EXE", "").strip()
BLENDER_TIMEOUT = int(os.getenv("BLENDER_TIMEOUT", "60"))
BLENDER_OUTPUT_DIR = os.getenv("BLENDER_OUTPUT_DIR", "outputs/blender").strip()

_FALLBACK_PATHS = [
    "/usr/bin/blender",
    "/usr/local/bin/blender",
]

# Placeholder injecté dans le script généré avant exécution.
# Le LLM doit écrire OUTPUT_BLEND_PATH dans ses appels wm.save_as_mainfile.
_OUTPUT_BLEND_PLACEHOLDER = "OUTPUT_BLEND_PATH"

_BLENDER_SYSTEM_PROMPT = """\
Tu es un expert Blender Python (bpy). Génère un script Python bpy valide qui crée la scène demandée.

Règles strictes :
- Ne jamais utiliser de chemins hardcodés pour les fichiers de sortie.
- Utiliser la variable OUTPUT_BLEND_PATH pour sauvegarder le fichier .blend.
- Terminer le script par : bpy.ops.wm.save_as_mainfile(filepath=OUTPUT_BLEND_PATH)
- Ne jamais appeler bpy.ops.render.render() ni lancer de rendu image.
- Ne pas utiliser import sys, os pour modifier les chemins de sortie.
- Le script doit être autonome et exécutable via blender --background --python.
- Répondre uniquement avec le code Python, dans un bloc ```python ... ```.
"""


def resolve_blender_exe() -> str | None:
    """
    Résout le chemin de l'exécutable Blender.
    Priorité : BLENDER_EXE env → fallbacks Linux → None si absent.
    """
    if BLENDER_EXE:
        resolved = shutil.which(BLENDER_EXE) or (BLENDER_EXE if Path(BLENDER_EXE).is_file() else None)
        if resolved:
            return resolved

    for path in _FALLBACK_PATHS:
        if Path(path).is_file():
            return path

    return None


def _extract_python_from_markdown(text: str) -> str:
    """
    Extrait le premier bloc de code Python d'une réponse markdown.
    Si aucun bloc n'est trouvé, retourne le texte tel quel (stripped).
    """
    match = re.search(r"```(?:python)?\s*\n(.*?)```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return text.strip()


def _inject_output_path(script: str, output_path: str) -> str:
    """
    Injecte OUTPUT_BLEND_PATH en tête du script (raw string pour les chemins Windows/Linux).
    Neutralise aussi tout save_as_mainfile avec un chemin hardcodé en le remplaçant
    par la version contrôlée.
    Ajoute TOUJOURS un bloc de sauvegarde forcée à la fin du script, avec le chemin
    canonique en string littérale — indépendant de OUTPUT_BLEND_PATH.
    Ainsi, même si le LLM réécrit OUTPUT_BLEND_PATH ou utilise une variable,
    la dernière sauvegarde pointe toujours vers le chemin attendu par le backend.
    """
    # Définir la variable en tête (pour les scripts qui l'utilisent correctement)
    header = f'OUTPUT_BLEND_PATH = r"{output_path}"\n'

    # Remplacer les éventuels save_as_mainfile(filepath="literal") hardcodés
    script = re.sub(
        r'bpy\.ops\.wm\.save_as_mainfile\s*\(\s*filepath\s*=\s*["\'][^"\']*["\']\s*\)',
        "bpy.ops.wm.save_as_mainfile(filepath=OUTPUT_BLEND_PATH)",
        script,
    )

    # Bloc de sauvegarde forcée vers le chemin canonique (string littérale, pas une variable).
    # Ajouté APRÈS tout le code LLM : le LLM ne peut pas l'écraser.
    canonical_save = (
        f'\n# -- aicore: forced canonical save --\n'
        f'import bpy as _bpy\n'
        f'_bpy.ops.wm.save_as_mainfile(filepath=r"{output_path}")\n'
    )

    return header + script + canonical_save


def build_blender_script(
    message: str,
    context: dict,
    request_id: str,
) -> BlenderRequest:
    """
    Génère un script bpy via Ollama, prépare le dossier contrôlé,
    injecte OUTPUT_BLEND_PATH et retourne une BlenderRequest.
    Le chemin output_path est imposé par le système, jamais décidé par le LLM.
    """
    output_dir = Path(BLENDER_OUTPUT_DIR) / request_id
    output_dir.mkdir(parents=True, exist_ok=True)

    script_path = str(output_dir / "scene.py")
    output_path = str(output_dir / "scene.blend")

    prompt = f"{_BLENDER_SYSTEM_PROMPT}\n\nDemande utilisateur : {message}"
    raw_response = generate_with_ollama("qwen2.5-coder:7b", prompt)

    raw_code = _extract_python_from_markdown(raw_response)
    final_script = _inject_output_path(raw_code, output_path)

    Path(script_path).write_text(final_script, encoding="utf-8")

    return BlenderRequest(
        request_id=request_id,
        script_content=final_script,
        script_path=script_path,
        output_path=output_path,
        output_dir=str(output_dir),
        timeout=BLENDER_TIMEOUT,
    )


def run_blender_script(request: BlenderRequest) -> BlenderResult:
    """
    Exécute Blender en background avec le script préparé.
    Retourne un BlenderResult avec status, returncode, stdout, stderr.
    Pas de shell=True. Timeout configurable via BLENDER_TIMEOUT.
    Si Blender est absent : status blender_not_found, pas de crash.
    """
    exe = resolve_blender_exe()
    if exe is None:
        return BlenderResult(
            status="blender_not_found",
            request_id=request.request_id,
            script_path=request.script_path,
            output_path=None,
            output_dir=request.output_dir,
            returncode=None,
            stdout=None,
            stderr=None,
            error="Blender executable not found. Set BLENDER_EXE or install Blender.",
        )

    try:
        proc = subprocess.run(
            [exe, "--background", "--python", request.script_path],
            capture_output=True,
            text=True,
            timeout=request.timeout,
        )
    except subprocess.TimeoutExpired:
        return BlenderResult(
            status="timeout",
            request_id=request.request_id,
            script_path=request.script_path,
            output_path=None,
            output_dir=request.output_dir,
            returncode=None,
            stdout=None,
            stderr=None,
            error=f"Blender exceeded timeout of {request.timeout}s.",
        )

    stdout = proc.stdout or ""
    stderr = proc.stderr or ""

    if proc.returncode != 0:
        return BlenderResult(
            status="error",
            request_id=request.request_id,
            script_path=request.script_path,
            output_path=None,
            output_dir=request.output_dir,
            returncode=proc.returncode,
            stdout=stdout,
            stderr=stderr,
            error=f"Blender exited with returncode {proc.returncode}.",
        )

    # Blender a terminé avec succès : vérifier que le .blend existe
    if not Path(request.output_path).exists():
        return BlenderResult(
            status="no_output",
            request_id=request.request_id,
            script_path=request.script_path,
            output_path=None,
            output_dir=request.output_dir,
            returncode=proc.returncode,
            stdout=stdout,
            stderr=stderr,
            error="Blender completed but no .blend file was produced.",
        )

    return BlenderResult(
        status="success",
        request_id=request.request_id,
        script_path=request.script_path,
        output_path=request.output_path,
        output_dir=request.output_dir,
        returncode=proc.returncode,
        stdout=stdout,
        stderr=stderr,
        error=None,
    )
