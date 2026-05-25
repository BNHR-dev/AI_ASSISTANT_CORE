"""
H.4.8 — Runtime contract validator product_render.

Fonctions PURES — pas d'I/O, pas de subprocess, pas de dépendance bpy.
Comparent la liste d'objets réellement présents dans `scene.blend` (collectée
par le subprocess d'inspection de blender_validator) à un contrat déclaratif
spécifique au template.

Politique V0 :
- Émission de violations namespacées `template_required_missing:<Name>`
  et `template_forbidden_object:<Name>`.
- Ces violations sont propagées dans `scene_report["violations"]` après
  application de la passe corrective (donc reflètent l'état final).
- L'état initial est préservé séparément dans
  `scene_report["runtime_contract"]["initial_violations"]`.

Différence avec validate_scene_py_against_template (H.4.3-C) :
- ce module-ci opère sur le résultat runtime (objets effectivement présents
  dans le .blend), pas sur le texte du scene.py.
- les violations sont namespacées différemment (`template_required_missing:`
  vs `missing_required:`) pour éviter toute collision et permettre de les
  filtrer / différencier dans les rapports.
"""
from __future__ import annotations


V_TEMPLATE_REQUIRED_MISSING_PREFIX = "template_required_missing:"
V_TEMPLATE_FORBIDDEN_OBJECT_PREFIX = "template_forbidden_object:"


# ---------------------------------------------------------------------------
# Specs déclaratives — contrat runtime par template
# ---------------------------------------------------------------------------
# Volontairement séparées de TEMPLATE_SPECS dans blender_templates.py :
# - TEMPLATE_SPECS sert à la validation statique scene.py (H.4.3-C, H.4.7)
# - RUNTIME_CONTRACT_SPECS sert à la validation runtime scene.blend (H.4.8)
#
# Les deux peuvent diverger : par exemple Fill_Light est requis runtime
# (présent dans le scaffold TEMPLATE_PRODUCT_RENDER) mais n'est PAS listé
# dans TEMPLATE_SPECS.required_objects en H.4.3-C (afin de ne pas durcir
# l'AST guard pré-exécution sur un nom de lumière secondaire).
# ---------------------------------------------------------------------------

RUNTIME_CONTRACT_SPECS: dict[str, dict[str, list[str]]] = {
    "product_render": {
        "required_objects": [
            "Backdrop_Plane",
            "Pedestal",
            "Product_Subject",
            "Camera",
            "Key_Light",
            "Fill_Light",
        ],
        "forbidden_objects": [
            "Sun",
        ],
    },
}


def get_runtime_contract_spec(template_name: str | None) -> dict[str, list[str]] | None:
    """Retourne la spec runtime du template, ou None si inconnu / template_name vide."""
    if not template_name:
        return None
    return RUNTIME_CONTRACT_SPECS.get(template_name)


def evaluate_runtime_contract(
    object_names: list[str] | None,
    template_name: str | None,
) -> dict:
    """
    Évalue le contrat runtime sur la liste des objets réellement présents.

    Paramètres
    ----------
    object_names  : liste des noms d'objets observés dans scene.blend
                    (typiquement `bpy_report["object_names"]` après
                    inspection bpy). None → status `skipped`.
    template_name : nom du template sélectionné. None ou inconnu → `skipped`.

    Retourne toujours un dict avec la forme :
      {
        "status": "passed" | "degraded" | "skipped",
        "template_name": str | None,
        "violations": list[str],
        "required_present": list[str],
        "required_missing": list[str],
        "forbidden_present": list[str],
      }

    Ne lève jamais d'exception.
    """
    spec = get_runtime_contract_spec(template_name)
    if spec is None or object_names is None:
        return {
            "status": "skipped",
            "template_name": template_name,
            "violations": [],
            "required_present": [],
            "required_missing": [],
            "forbidden_present": [],
        }

    name_set = set(object_names)
    required = spec.get("required_objects", []) or []
    forbidden = spec.get("forbidden_objects", []) or []

    required_present = [n for n in required if n in name_set]
    required_missing = [n for n in required if n not in name_set]
    forbidden_present = [n for n in forbidden if n in name_set]

    violations: list[str] = []
    for name in required_missing:
        violations.append(f"{V_TEMPLATE_REQUIRED_MISSING_PREFIX}{name}")
    for name in forbidden_present:
        violations.append(f"{V_TEMPLATE_FORBIDDEN_OBJECT_PREFIX}{name}")

    status = "passed" if not violations else "degraded"

    return {
        "status": status,
        "template_name": template_name,
        "violations": violations,
        "required_present": required_present,
        "required_missing": required_missing,
        "forbidden_present": forbidden_present,
    }
