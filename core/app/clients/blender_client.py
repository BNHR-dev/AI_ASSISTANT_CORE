from __future__ import annotations

import os
import re
import shutil
import subprocess
import textwrap
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

Recette obligatoire :
1. Commencer par : import bpy
2. Supprimer les objets par défaut :
   bpy.ops.object.select_all(action='SELECT')
   bpy.ops.object.delete()
3. Créer les objets demandés :
   - Pour un cube : bpy.ops.mesh.primitive_cube_add(size=2, location=(0, 0, 0))
   - Ne jamais utiliser bpy.data.meshes.new() sans appeler from_pydata() avec des vertices et faces réels.
4. Pour un matériau métallique :
   - Créer un matériau avec use_nodes=True
   - Utiliser le Principled BSDF : noeud.inputs["Metallic"].default_value = 1.0, Roughness=0.2
   - Ne jamais appeler nodes.clear() puis accéder à un nœud supprimé.
5. Ajouter une caméra pour les scènes :
   bpy.ops.object.camera_add(location=(7, -7, 5))
   bpy.context.scene.camera = bpy.context.object
6. Ajouter une lumière :
   bpy.ops.object.light_add(type='SUN', location=(4, 4, 6))
7. Nommer les objets clairement : ex. "Cube_Metal", "Camera", "Key_Light"
8. Terminer par : bpy.ops.wm.save_as_mainfile(filepath=OUTPUT_BLEND_PATH)

Interdits stricts :
- Ne jamais utiliser de chemins hardcodés pour les fichiers de sortie.
- Ne pas appeler bpy.ops.render.render() librement — le pipeline gère le rendu via OUTPUT_RENDER_PATH.
- Ne pas utiliser import sys, os pour modifier les chemins de sortie.
- Ne pas utiliser bpy.path.abspath() pour construire le chemin de sortie.

Le script doit être autonome et exécutable via blender --background --python.
Répondre uniquement avec le code Python, dans un bloc ```python ... ```.
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


def _inject_output_paths(script: str, output_path: str, render_path: str) -> str:
    """
    Enveloppe le script LLM dans un try/finally pour garantir :
    1. La sauvegarde canonique .blend même si le script LLM plante.
    2. Un contenu minimal (mesh, caméra, lumière) si le LLM a produit une scène vide.

    Le rendu PNG preview est géré séparément dans un second subprocess Blender
    (voir run_blender_script) afin qu'un crash du rendu ne fasse pas échouer le pipeline.

    Structure produite :

        OUTPUT_BLEND_PATH = r"<output_path>"
        OUTPUT_RENDER_PATH = r"<render_path>"

        try:
            <script LLM indenté>
        finally:
            import bpy as _bpy
            # fallbacks mesh / caméra / lumière
            ...
            # sauvegarde canonique .blend
            _bpy.ops.wm.save_as_mainfile(filepath=r"<output_path>")

    Les chemins sont injectés en string littérales — jamais via des variables LLM.
    """
    # Variables en tête (pour les scripts qui les utilisent correctement)
    header = (
        f'OUTPUT_BLEND_PATH = r"{output_path}"\n'
        f'OUTPUT_RENDER_PATH = r"{render_path}"\n\n'
    )

    # Remplacer les éventuels save_as_mainfile(filepath="literal") hardcodés dans le script LLM
    script = re.sub(
        r'bpy\.ops\.wm\.save_as_mainfile\s*\(\s*filepath\s*=\s*["\'][^"\']*["\']\s*\)',
        "bpy.ops.wm.save_as_mainfile(filepath=OUTPUT_BLEND_PATH)",
        script,
    )

    # Indenter le script LLM pour l'intégrer dans le try
    indented_script = textwrap.indent(script, "    ")

    # Bloc finally : fallbacks contenu minimal + sauvegarde .blend canonique uniquement
    # Le rendu PNG est dans un subprocess séparé (best-effort, ne bloque pas success)
    finally_block = (
        f'finally:\n'
        f'    import bpy as _bpy\n'
        f'    # -- aicore: fallback contenu minimal --\n'
        f'    if not any(o.type == "MESH" for o in _bpy.context.scene.objects):\n'
        f'        _bpy.ops.mesh.primitive_cube_add(size=2, location=(0, 0, 0))\n'
        f'    if not any(o.type == "CAMERA" for o in _bpy.context.scene.objects):\n'
        f'        _bpy.ops.object.camera_add(location=(7, -7, 5))\n'
        f'        _bpy.context.scene.camera = _bpy.context.object\n'
        f'    if not any(o.type == "LIGHT" for o in _bpy.context.scene.objects):\n'
        f'        _bpy.ops.object.light_add(type="SUN", location=(4, 4, 6))\n'
        f'    # -- aicore: forced canonical save --\n'
        f'    _bpy.ops.wm.save_as_mainfile(filepath=r"{output_path}")\n'
    )

    return header + "try:\n" + indented_script + "\n" + finally_block


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
    render_path = str(output_dir / "preview.png")

    prompt = f"{_BLENDER_SYSTEM_PROMPT}\n\nDemande utilisateur : {message}"
    raw_response = generate_with_ollama("qwen2.5-coder:7b", prompt)

    raw_code = _extract_python_from_markdown(raw_response)
    final_script = _inject_output_paths(raw_code, output_path, render_path)

    Path(script_path).write_text(final_script, encoding="utf-8")

    return BlenderRequest(
        request_id=request_id,
        script_content=final_script,
        script_path=script_path,
        output_path=output_path,
        render_path=render_path,
        output_dir=str(output_dir),
        timeout=BLENDER_TIMEOUT,
    )


def _render_preview(exe: str, request: BlenderRequest) -> str | None:
    """
    Lance un second subprocess Blender best-effort pour produire preview.png depuis le .blend.
    Écrit un script temporaire render_preview.py dans output_dir, l'exécute, puis le supprime.
    Retourne le chemin du PNG si produit, None sinon. Ne lève jamais d'exception.

    Le script oriente la caméra active vers le centre des objets MESH présents
    (fallback : origine) via mathutils, pour éviter un rendu vide/gris.
    """
    render_script_path = Path(request.output_dir) / "render_preview.py"
    render_script = (
        f'import bpy\n'
        f'from mathutils import Vector\n'
        f'\n'
        f'# -- Caméra active : créer si absente --\n'
        f'cam_obj = bpy.context.scene.camera\n'
        f'if cam_obj is None:\n'
        f'    bpy.ops.object.camera_add(location=(7, -7, 5))\n'
        f'    cam_obj = bpy.context.object\n'
        f'    bpy.context.scene.camera = cam_obj\n'
        f'\n'
        f'# -- Cible : centre de la bounding box monde des MESH, fallback origine --\n'
        f'# Utilise matrix_world @ bound_box pour tenir compte des transformations appliquées.\n'
        f'mesh_objects = [o for o in bpy.context.scene.objects if o.type == "MESH"]\n'
        f'if mesh_objects:\n'
        f'    all_corners = [\n'
        f'        o.matrix_world @ Vector(corner)\n'
        f'        for o in mesh_objects\n'
        f'        for corner in o.bound_box\n'
        f'    ]\n'
        f'    min_corner = Vector((\n'
        f'        min(v.x for v in all_corners),\n'
        f'        min(v.y for v in all_corners),\n'
        f'        min(v.z for v in all_corners),\n'
        f'    ))\n'
        f'    max_corner = Vector((\n'
        f'        max(v.x for v in all_corners),\n'
        f'        max(v.y for v in all_corners),\n'
        f'        max(v.z for v in all_corners),\n'
        f'    ))\n'
        f'    target = (min_corner + max_corner) / 2.0\n'
        f'    radius = max((max_corner - min_corner).length / 2.0, 1.0)\n'
        f'else:\n'
        f'    target = Vector((0.0, 0.0, 0.0))\n'
        f'    radius = 1.0\n'
        f'\n'
        f'# -- Repositionner la caméra à distance dépendante du radius --\n'
        f'distance = max(radius * 2.5, 5.0)\n'
        f'cam_obj.location = target + Vector((distance * 0.7, -distance * 0.7, distance * 0.5))\n'
        f'\n'
        f'# -- Orienter la caméra vers la cible --\n'
        f'direction = target - Vector(cam_obj.location)\n'
        f'if direction.length > 0:\n'
        f'    cam_obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()\n'
        f'\n'
        f'# -- Lumière SUN best-effort si aucune lumière dans la scène --\n'
        f'if not any(o.type == "LIGHT" for o in bpy.context.scene.objects):\n'
        f'    bpy.ops.object.light_add(type="SUN", location=(4, 4, 6))\n'
        f'\n'
        f'# -- Rendu PNG --\n'
        f'bpy.context.scene.render.image_settings.file_format = "PNG"\n'
        f'bpy.context.scene.render.filepath = r"{request.render_path}"\n'
        f'bpy.ops.render.render(write_still=True)\n'
    )
    try:
        render_script_path.write_text(render_script, encoding="utf-8")
        proc = subprocess.run(
            [exe, "--background", request.output_path, "--python", str(render_script_path)],
            capture_output=True,
            text=True,
            timeout=request.timeout,
        )
        if proc.returncode == 0 and Path(request.render_path).exists():
            return request.render_path
    except Exception:
        pass
    finally:
        try:
            render_script_path.unlink(missing_ok=True)
        except Exception:
            pass
    return None


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
            render_path=None,
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
            render_path=None,
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
            render_path=None,
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
            render_path=None,
            output_dir=request.output_dir,
            returncode=proc.returncode,
            stdout=stdout,
            stderr=stderr,
            error="Blender completed but no .blend file was produced.",
        )

    # PNG preview : second subprocess best-effort depuis le .blend produit.
    # Un crash ou une erreur du rendu ne fait pas échouer le pipeline.
    render_path = _render_preview(exe, request)

    return BlenderResult(
        status="success",
        request_id=request.request_id,
        script_path=request.script_path,
        output_path=request.output_path,
        render_path=render_path,
        output_dir=request.output_dir,
        returncode=proc.returncode,
        stdout=stdout,
        stderr=stderr,
        error=None,
    )
