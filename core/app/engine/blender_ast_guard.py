"""
H.4.7 — AST guard V0 pré-exécution Blender.

Analyse le code Python bpy brut produit par le LLM AVANT injection du header
contrôlé (`OUTPUT_BLEND_PATH`) et AVANT exécution Blender. Détecte les
patterns d'hallucination courants observés sur qwen2.5-coder:7b :
imports de fichiers externes, chemins placeholder, mesh vide, script
minuscule, etc.

Politique V0 : signal-only. Aucune violation détectée par ce module ne
bloque l'exécution Blender ni n'est propagée dans `scene_report["violations"]`.
Le rapport est exposé tel quel dans `scene_report["ast_guard"]`, au même
niveau que `visual_qa`, pour observabilité.

Fonctions PURES, pas d'I/O, pas de dépendance bpy / Blender / VM. Testable
hors VM.
"""
from __future__ import annotations

import ast
import re

from app.engine.blender_templates import get_template_spec


# ---------------------------------------------------------------------------
# Violations V0 — strings stables, exposées comme constantes nommées
# ---------------------------------------------------------------------------

V_AST_UNPARSEABLE            = "ast_unparseable"
V_EXTERNAL_ASSET_PREFIX      = "external_asset_loaded:"        # +<ext>
V_OPEN_NON_PIPELINE_FILE     = "open_non_pipeline_file"
V_PLACEHOLDER_PATH           = "placeholder_path"
V_NO_PRIMITIVE_ADD           = "no_primitive_add"
V_MESHES_NEW_WITHOUT_GEOMETRY = "meshes_new_without_geometry"
V_NO_CAMERA_ASSIGNMENT       = "no_camera_assignment"
V_SCRIPT_TOO_SHORT           = "script_too_short"
V_TEMPLATE_REQUIRED_PREFIX   = "template_required_missing:"    # +<Name>
V_TEMPLATE_FORBIDDEN_PREFIX  = "template_forbidden_prefix:"    # +<Prefix>
V_FALLBACK_CUBE_SUN_ONLY     = "fallback_cube_sun_only"
V_NO_DELETE_DEFAULT          = "no_delete_default"


# ---------------------------------------------------------------------------
# Seuils V0 — modifiables sans toucher à la logique
# ---------------------------------------------------------------------------

MIN_SCRIPT_LINES        = 8
MIN_SCRIPT_CHARS        = 200

# Extensions d'assets 3D externes interdits dans un script LLM
_EXTERNAL_ASSET_EXTS = ("obj", "fbx", "gltf", "glb", "dae", "abc", "ply", "usd", "usda", "usdc")

# Patterns de chemins placeholder dans les string literals
_PLACEHOLDER_PATTERNS = (
    re.compile(r"/path/to/", re.IGNORECASE),
    re.compile(r"\bpath_to_\w+", re.IGNORECASE),
    re.compile(r"\byour[_-]?\w+\.(?:obj|fbx|gltf|glb|png|jpg|jpeg)\b", re.IGNORECASE),
    re.compile(r"<[a-z0-9_./-]+>", re.IGNORECASE),
    re.compile(r"\bplaceholder\b", re.IGNORECASE),
    re.compile(r"\bmodel\.(?:obj|fbx|gltf|glb)\b", re.IGNORECASE),
    re.compile(r"\btexture\.(?:png|jpg|jpeg)\b", re.IGNORECASE),
)

# Noms de variables OUTPUT_* injectées par le pipeline (jamais des placeholders)
_PIPELINE_OUTPUT_VARS = ("OUTPUT_BLEND_PATH", "OUTPUT_RENDER_PATH")


# ---------------------------------------------------------------------------
# Helpers AST — extraction d'identifiants composés type bpy.ops.mesh.x
# ---------------------------------------------------------------------------

def _attr_chain(node: ast.AST) -> str | None:
    """
    Retourne la chaîne 'a.b.c' pour un node ast.Attribute imbriqué ancré
    sur un ast.Name. Renvoie None si la racine n'est pas un Name.
    Exemple : `bpy.ops.mesh.primitive_cube_add` → "bpy.ops.mesh.primitive_cube_add".
    """
    parts: list[str] = []
    current: ast.AST = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return None
    parts.append(current.id)
    return ".".join(reversed(parts))


def _iter_calls(tree: ast.AST):
    """Yield tous les ast.Call et leur chaîne d'attribut résolue."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            chain = _attr_chain(node.func)
            yield node, chain


def _iter_string_literals(tree: ast.AST):
    """Yield toutes les string constantes (ast.Constant str)."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.value


# ---------------------------------------------------------------------------
# Checks individuels — fonctions PURES, testables indépendamment
# ---------------------------------------------------------------------------

def _check_no_external_assets(tree: ast.AST) -> tuple[list[str], int]:
    """
    Détecte les appels d'import 3D externe :
      bpy.ops.import_scene.obj / fbx / gltf
      bpy.ops.wm.obj_import / fbx_import / usd_import
    Retourne (violations, count).
    """
    violations: list[str] = []
    count = 0
    for _node, chain in _iter_calls(tree):
        if not chain:
            continue
        # bpy.ops.import_scene.<ext>
        if chain.startswith("bpy.ops.import_scene."):
            ext = chain.rsplit(".", 1)[-1].lower()
            violations.append(f"{V_EXTERNAL_ASSET_PREFIX}{ext}")
            count += 1
            continue
        # bpy.ops.wm.<ext>_import
        if chain.startswith("bpy.ops.wm.") and chain.endswith("_import"):
            tail = chain.rsplit(".", 1)[-1]
            ext = tail[: -len("_import")].lower()
            if ext in _EXTERNAL_ASSET_EXTS:
                violations.append(f"{V_EXTERNAL_ASSET_PREFIX}{ext}")
                count += 1
    return violations, count


def _check_open_non_pipeline_file(tree: ast.AST) -> list[str]:
    """
    Détecte un appel `open(...)` ou `bpy.data.images.load(...)` sur un chemin
    littéral qui n'est pas une variable du pipeline (OUTPUT_BLEND_PATH, etc.).
    Best-effort : si le premier argument est une variable, on laisse passer.
    """
    violations: list[str] = []
    for node, chain in _iter_calls(tree):
        is_open = chain == "open"
        is_img_load = chain == "bpy.data.images.load"
        if not (is_open or is_img_load):
            continue
        if not node.args:
            continue
        arg = node.args[0]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            # Chemin littéral : suspect par défaut
            violations.append(V_OPEN_NON_PIPELINE_FILE)
            return violations  # une seule occurrence suffit en V0
        if isinstance(arg, ast.Name) and arg.id in _PIPELINE_OUTPUT_VARS:
            continue
    return violations


def _check_placeholder_paths(tree: ast.AST) -> list[str]:
    """
    Scanne les string literals pour patterns placeholder évidents.
    """
    for literal in _iter_string_literals(tree):
        for pattern in _PLACEHOLDER_PATTERNS:
            if pattern.search(literal):
                return [V_PLACEHOLDER_PATH]
    return []


def _collect_primitive_adds(tree: ast.AST) -> list[str]:
    """
    Retourne la liste des noms d'opérateurs bpy.ops.mesh.primitive_*_add
    appelés dans le script. Vide si aucun.
    """
    found: list[str] = []
    for _node, chain in _iter_calls(tree):
        if not chain:
            continue
        if chain.startswith("bpy.ops.mesh.primitive_") and chain.endswith("_add"):
            found.append(chain.rsplit(".", 1)[-1])
    return found


def _has_from_pydata_with_data(tree: ast.AST) -> bool:
    """
    True si au moins un appel `<x>.from_pydata(verts, edges, faces)` est
    présent ET aucun de ses arguments n'est une liste vide littérale.
    Best-effort : un from_pydata avec variables est considéré comme valide.
    """
    for node, chain in _iter_calls(tree):
        if not chain or not chain.endswith(".from_pydata"):
            continue
        if not node.args:
            continue
        # Si tous les arguments sont des listes vides littérales → invalide
        all_empty = True
        for arg in node.args:
            if isinstance(arg, ast.List) and not arg.elts:
                continue
            all_empty = False
            break
        if not all_empty:
            return True
    return False


def _check_primitive_geometry(tree: ast.AST, primitives: list[str]) -> list[str]:
    """V_NO_PRIMITIVE_ADD si ni primitive_*_add, ni from_pydata avec data."""
    if primitives:
        return []
    if _has_from_pydata_with_data(tree):
        return []
    return [V_NO_PRIMITIVE_ADD]


def _check_meshes_new_without_geometry(tree: ast.AST) -> list[str]:
    """
    bpy.data.meshes.new(...) est suspect SI aucun from_pydata avec data
    n'est présent dans le script.
    """
    has_meshes_new = False
    for _node, chain in _iter_calls(tree):
        if chain == "bpy.data.meshes.new":
            has_meshes_new = True
            break
    if not has_meshes_new:
        return []
    if _has_from_pydata_with_data(tree):
        return []
    return [V_MESHES_NEW_WITHOUT_GEOMETRY]


def _check_camera_assignment(tree: ast.AST) -> list[str]:
    """
    V_NO_CAMERA_ASSIGNMENT si aucun de :
      - bpy.ops.object.camera_add(...)
      - bpy.context.scene.camera = ...
    """
    has_camera_add = False
    for _node, chain in _iter_calls(tree):
        if chain == "bpy.ops.object.camera_add":
            has_camera_add = True
            break
    if has_camera_add:
        return []

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if _attr_chain(target) == "bpy.context.scene.camera":
                    return []
    return [V_NO_CAMERA_ASSIGNMENT]


def _check_script_min_size(raw_code: str) -> list[str]:
    """V_SCRIPT_TOO_SHORT si lignes non vides < MIN_SCRIPT_LINES ou chars < MIN_SCRIPT_CHARS."""
    if len(raw_code) < MIN_SCRIPT_CHARS:
        return [V_SCRIPT_TOO_SHORT]
    non_empty_lines = [ln for ln in raw_code.splitlines() if ln.strip()]
    if len(non_empty_lines) < MIN_SCRIPT_LINES:
        return [V_SCRIPT_TOO_SHORT]
    return []


def _check_template_objects(raw_code: str, template_name: str | None) -> list[str]:
    """
    Réutilise la spec déclarative `TEMPLATE_SPECS` pour produire des
    violations namespacées AST guard (séparées du namespace scene_report).
    """
    if not template_name:
        return []
    spec = get_template_spec(template_name)
    if spec is None:
        return []

    violations: list[str] = []
    for obj_name in spec.get("required_objects", []):
        if obj_name and obj_name not in raw_code:
            violations.append(f"{V_TEMPLATE_REQUIRED_PREFIX}{obj_name}")
    for prefix in spec.get("forbidden_prefixes", []):
        if not prefix:
            continue
        pattern = re.escape(prefix) + r"\w"
        if re.search(pattern, raw_code):
            violations.append(f"{V_TEMPLATE_FORBIDDEN_PREFIX}{prefix}")
    return violations


def _check_fallback_cube_sun_only(tree: ast.AST, primitives: list[str]) -> list[str]:
    """
    Heuristique "défausse minimale" : exactement un primitive_add et c'est
    primitive_cube_add, exactement un light_add, exactement un camera_add,
    et aucun bpy.data.meshes.new. Signal soft d'un script LLM minimal.
    """
    if len(primitives) != 1 or primitives[0] != "primitive_cube_add":
        return []
    light_add_count = 0
    camera_add_count = 0
    has_meshes_new = False
    for _node, chain in _iter_calls(tree):
        if chain == "bpy.ops.object.light_add":
            light_add_count += 1
        elif chain == "bpy.ops.object.camera_add":
            camera_add_count += 1
        elif chain == "bpy.data.meshes.new":
            has_meshes_new = True
    if has_meshes_new:
        return []
    if light_add_count == 1 and camera_add_count == 1:
        return [V_FALLBACK_CUBE_SUN_ONLY]
    return []


def _check_delete_default(raw_code: str) -> list[str]:
    """
    Détection textuelle (suffisante en V0) de l'étape cleanup standard :
      bpy.ops.object.select_all(...) suivi de bpy.ops.object.delete(...).
    Absent dans la majorité des scripts d'hallucination.
    """
    if "bpy.ops.object.select_all" in raw_code and "bpy.ops.object.delete" in raw_code:
        return []
    return [V_NO_DELETE_DEFAULT]


# ---------------------------------------------------------------------------
# Orchestrateur public
# ---------------------------------------------------------------------------

def _empty_checks() -> dict:
    return {
        "ast_parseable":              {"status": "skipped"},
        "no_external_assets":         {"status": "skipped"},
        "no_placeholder_paths":       {"status": "skipped"},
        "has_primitive_geometry":    {"status": "skipped"},
        "meshes_new_has_from_pydata": {"status": "skipped"},
        "template_required_objects":  {"status": "skipped"},
        "script_min_size":            {"status": "skipped"},
        "active_camera_assigned":     {"status": "skipped"},
        "fallback_cube_sun_only":     {"status": "skipped"},
        "delete_default_present":     {"status": "skipped"},
    }


def _wrap(check_violations: list[str]) -> dict:
    """Petit helper : passed si aucune violation, degraded sinon."""
    return {
        "status": "degraded" if check_violations else "passed",
        "violations": check_violations,
    }


def analyze_scene_py(raw_code: str, template_name: str | None) -> dict:
    """
    Analyse statique pré-exécution du scene.py LLM.

    Paramètres
    ----------
    raw_code      : texte du script Python bpy tel que produit par le LLM,
                    après extraction markdown mais AVANT injection des
                    OUTPUT_*_PATH et du try/finally pipeline.
    template_name : nom du template sélectionné (product_render, interior_space,
                    ou None pour un prompt libre).

    Retourne
    --------
    Un dict avec la même forme que `visual_qa` :
      - status      : "passed" | "degraded" | "skipped"
      - violations  : liste agrégée des violations remontées par les checks
      - checks      : détail par check (status + violations)
      - metrics     : compteurs utiles à l'observabilité

    Ne lève jamais d'exception.
    """
    if not isinstance(raw_code, str) or not raw_code.strip():
        return {
            "status": "skipped",
            "violations": [],
            "checks": _empty_checks(),
            "metrics": {
                "raw_code_length": 0,
                "primitive_add_count": 0,
                "external_load_count": 0,
                "ast_parse_error": None,
            },
        }

    # 1. Parse AST — best-effort
    try:
        tree = ast.parse(raw_code)
        ast_parse_error: str | None = None
    except SyntaxError as exc:
        # AST non parseable : on retourne un rapport partiel, sans relancer.
        # Le pipeline reste libre d'exécuter (Blender plantera proprement).
        checks = _empty_checks()
        checks["ast_parseable"] = {
            "status": "degraded",
            "violations": [V_AST_UNPARSEABLE],
            "details": str(exc),
        }
        # Les checks textuels restent applicables.
        size_v = _check_script_min_size(raw_code)
        template_v = _check_template_objects(raw_code, template_name)
        delete_v = _check_delete_default(raw_code)
        checks["script_min_size"]           = _wrap(size_v)
        checks["template_required_objects"] = _wrap(template_v)
        checks["delete_default_present"]    = _wrap(delete_v)

        violations: list[str] = [V_AST_UNPARSEABLE]
        violations.extend(size_v)
        violations.extend(template_v)
        violations.extend(delete_v)
        return {
            "status": "degraded",
            "violations": violations,
            "checks": checks,
            "metrics": {
                "raw_code_length": len(raw_code),
                "primitive_add_count": 0,
                "external_load_count": 0,
                "ast_parse_error": str(exc),
            },
        }

    # 2. Checks AST-based
    external_v, external_count = _check_no_external_assets(tree)
    open_v                     = _check_open_non_pipeline_file(tree)
    placeholder_v              = _check_placeholder_paths(tree)
    primitives                 = _collect_primitive_adds(tree)
    primitive_v                = _check_primitive_geometry(tree, primitives)
    meshes_new_v               = _check_meshes_new_without_geometry(tree)
    camera_v                   = _check_camera_assignment(tree)
    fallback_v                 = _check_fallback_cube_sun_only(tree, primitives)

    # 3. Checks texte-based
    size_v     = _check_script_min_size(raw_code)
    template_v = _check_template_objects(raw_code, template_name)
    delete_v   = _check_delete_default(raw_code)

    checks = {
        "ast_parseable":              {"status": "passed", "violations": []},
        "no_external_assets":         _wrap(external_v + open_v),
        "no_placeholder_paths":       _wrap(placeholder_v),
        "has_primitive_geometry":     _wrap(primitive_v),
        "meshes_new_has_from_pydata": _wrap(meshes_new_v),
        "template_required_objects":  _wrap(template_v),
        "script_min_size":            _wrap(size_v),
        "active_camera_assigned":     _wrap(camera_v),
        "fallback_cube_sun_only":     _wrap(fallback_v),
        "delete_default_present":     _wrap(delete_v),
    }

    violations: list[str] = []
    for check in checks.values():
        violations.extend(check.get("violations", []))

    return {
        "status": "degraded" if violations else "passed",
        "violations": violations,
        "checks": checks,
        "metrics": {
            "raw_code_length": len(raw_code),
            "primitive_add_count": len(primitives),
            "external_load_count": external_count,
            "ast_parse_error": ast_parse_error,
        },
    }
