"""
Blender Blocking Contract Lite — vérification statique d'un script bpy.

Vérifie qu'un script de blocking produit par le pipeline respecte les critères
minimaux d'une scène de blocking valide. Analyse statique uniquement : aucun
import bpy, aucune exécution Blender, aucun accès filesystem.

Utilisé dans les tests et comme warning interne.
N'est PAS un gate dur runtime pour cette phase : il ne bloque pas /execute.

Violations détectées :
- no_floor_or_space         : aucun objet pouvant faire office de sol/espace détecté
- no_named_subject          : aucun objet avec un nom explicite détecté
- hardcoded_output_path     : chemin de sortie hardcodé dans le script
- scene_likely_empty        : aucune primitive ni mesh détecté
- no_camera_in_script       : aucun camera_add et aucun CAMERA détecté
- no_light_in_script        : aucun light_add et aucun LIGHT détecté
"""

from __future__ import annotations

import re


# Patterns indicatifs d'un sol ou repère spatial
_FLOOR_SIGNALS = (
    "floor", "sol", "ground", "plane",
    "primitive_plane_add",
    "primitive_cube_add",   # peut servir de sol si aplati
    '"Floor"', "'Floor'",
    '"Sol"', "'Sol'",
    '"Ground"', "'Ground'",
    "location=(0, 0, 0)",
    "location=(0,0,0)",
)

# Signaux indiquant un nom explicite d'objet
_NAMED_OBJECT_SIGNALS = (
    'name="',
    "name='",
    '"Subject"', "'Subject'",
    '"Sujet"', "'Sujet'",
    '"Character"', "'Character'",
    '"Personnage"', "'Personnage'",
    '"Proxy"', "'Proxy'",
    '"Main_Subject"', "'Main_Subject'",
    '"Hero"', "'Hero'",
)

# Patterns de chemins hardcodés (hors OUTPUT_BLEND_PATH géré par le pipeline).
# Détecte filepath="..." ou path="..." avec un contenu qui ressemble à un vrai chemin
# (contient / ou \ ou : suivi d'autres chars) et n'est pas OUTPUT_BLEND_PATH.
_HARDCODED_PATH_RE = re.compile(
    r'(?:filepath|path)\s*=\s*["\']([^"\']+)["\']'
)
_OUTPUT_BLEND_VAR = "OUTPUT_BLEND_PATH"

# Signaux de présence d'une primitive ou mesh
_GEOMETRY_SIGNALS = (
    "primitive_cube_add",
    "primitive_sphere_add",
    "primitive_cylinder_add",
    "primitive_plane_add",
    "primitive_cone_add",
    "primitive_torus_add",
    "from_pydata",
    "bpy.data.meshes.new",
)

_CAMERA_SIGNALS = ("camera_add", "CAMERA")
_LIGHT_SIGNALS = ("light_add", "LIGHT", "SUN", "POINT", "SPOT", "AREA")


def check_blender_blocking_contract(script: str) -> dict:
    """
    Analyse statique d'un script bpy vis-à-vis du contrat de blocking lite.

    Retourne :
        {
            "static_contract_violations": list[str],
            "static_contract_passed": bool,
        }

    static_contract_passed = True si aucune violation.
    Le résultat est informatif — ne bloque pas le pipeline runtime.
    """
    violations: list[str] = []

    # Scène probablement vide
    if not any(sig in script for sig in _GEOMETRY_SIGNALS):
        violations.append("scene_likely_empty")

    # Aucun sol / repère spatial détecté
    if not any(sig in script for sig in _FLOOR_SIGNALS):
        violations.append("no_floor_or_space")

    # Aucun objet avec nom explicite
    if not any(sig in script for sig in _NAMED_OBJECT_SIGNALS):
        violations.append("no_named_subject")

    # Chemin hardcodé hors OUTPUT_BLEND_PATH.
    # Cherche filepath="..." ou path="..." avec un contenu qui ressemble à un chemin
    # (contient / ou \ ou :) et n'est pas la variable OUTPUT_BLEND_PATH.
    for m in _HARDCODED_PATH_RE.finditer(script):
        value = m.group(1)
        if _OUTPUT_BLEND_VAR not in value and (
            "/" in value or "\\" in value or ":" in value
        ):
            violations.append("hardcoded_output_path")
            break

    # Aucune caméra
    if not any(sig in script for sig in _CAMERA_SIGNALS):
        violations.append("no_camera_in_script")

    # Aucune lumière
    if not any(sig in script for sig in _LIGHT_SIGNALS):
        violations.append("no_light_in_script")

    return {
        "static_contract_violations": violations,
        "static_contract_passed": len(violations) == 0,
    }
