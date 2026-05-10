from __future__ import annotations

import re


_BLENDER_REQUEST_SIGNALS = (
    "blender", "bpy", "script blender", "blender script",
    "modéliser", "modele 3d", "créer une scène", "scène blender",
    "créer un cube", "create a cube",
)

_BLENDER_OUTPUT_SIGNALS = (
    "import bpy",
    "bpy.ops",
    "bpy.data",
    "bpy.context",
)

_RENDER_EXPLICIT_SIGNALS = (
    "render", "rendu", "renderise",
    "lance le rendu", "génère le rendu",
    "do a render", "launch render",
)

_CUBE_REQUEST_SIGNALS = ("cube", "cubo")

_METALLIC_REQUEST_SIGNALS = ("métallique", "metallic", "metal", "acier", "chrome")

_SCENE_REQUEST_SIGNALS = (
    "scène", "scene", "créer une scène", "create a scene",
    "crée une scène", "blender scene",
)

_NODES_CLEAR_RE = re.compile(r"nodes\.clear\s*\(\s*\)")
_NODES_ACCESS_AFTER_CLEAR_RE = re.compile(r"nodes\s*[\[.]")

_EMPTY_NAMED_MESH_RE = re.compile(
    r'bpy\.data\.meshes\.new\s*\(\s*["\'](?:Cube|Sphere)["\']'
)

_FROM_PYDATA_EMPTY_RE = re.compile(
    r"from_pydata\s*\(\s*\[\s*\]\s*,\s*\[\s*\]\s*,\s*\[\s*\]\s*\)"
)


def detect_blender_script(text: str) -> bool:
    lowered = text.lower()
    return any(sig in lowered for sig in _BLENDER_OUTPUT_SIGNALS)


def _is_blender_request(message: str) -> bool:
    lowered = message.lower()
    return any(sig in lowered for sig in _BLENDER_REQUEST_SIGNALS)


def analyze_blender_script_quality(message: str, output: str) -> dict:
    if not _is_blender_request(message) and not detect_blender_script(output):
        return {"is_blender": False, "violations": []}

    violations: list[str] = []
    msg_lower = message.lower()

    if "import bpy" not in output:
        violations.append("import_bpy_missing")

    if "bpy.ops.render.render" in output:
        if not any(sig in msg_lower for sig in _RENDER_EXPLICIT_SIGNALS):
            violations.append("render_called_without_request")

    if "subprocess" in output or "os.system" in output:
        violations.append("subprocess_or_os_system_forbidden")

    if "bpy.data.meshes.new" in output and "from_pydata" not in output:
        violations.append("meshes_new_without_from_pydata")

    if _FROM_PYDATA_EMPTY_RE.search(output):
        violations.append("from_pydata_without_vertices_faces")

    if any(sig in msg_lower for sig in _CUBE_REQUEST_SIGNALS):
        if "primitive_cube_add" not in output and "from_pydata" not in output:
            violations.append("cube_requested_without_geometry_api")

    if _EMPTY_NAMED_MESH_RE.search(output) and "from_pydata" not in output:
        violations.append("empty_named_mesh_without_geometry")

    # nodes.clear() suivi d'un accès à un nœud (nodes[...] ou nodes.get(...))
    # Pattern cassant : nœuds supprimés puis référencés
    if _NODES_CLEAR_RE.search(output) and _NODES_ACCESS_AFTER_CLEAR_RE.search(output):
        violations.append("nodes_clear_then_node_access")

    # Matériau métallique demandé mais Metallic non configuré dans le script
    if any(sig in msg_lower for sig in _METALLIC_REQUEST_SIGNALS):
        if "Metallic" not in output and "metallic" not in output:
            violations.append("metallic_requested_without_metallic_value")

    # Scène demandée mais aucune caméra créée dans le script
    # Informatif uniquement — le fallback finally en crée une si besoin
    if any(sig in msg_lower for sig in _SCENE_REQUEST_SIGNALS):
        if "camera_add" not in output and "CAMERA" not in output:
            violations.append("camera_missing_in_script")

    return {"is_blender": True, "violations": violations}
