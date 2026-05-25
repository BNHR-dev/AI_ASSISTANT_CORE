"""
H.4.8 — Runtime corrector product_render.

Génère et exécute un script bpy déterministe sur un `scene.blend` déjà produit
pour combler les manques du contrat runtime (Key_Light / Fill_Light absents),
neutraliser les objets parasites (Sun) et réappliquer un cadrage caméra
canonique product_render. Re-rend `preview.png` dans la foulée.

Politique V0 (signal-only NON applicable ici — la passe est corrective) :
- correction déclenchée UNIQUEMENT si `template_name == "product_render"`
  ET `Product_Subject` est présent dans la scène initiale ;
- aucune correction si Product_Subject est absent : la phase ne tente pas
  de deviner un meilleur cadrage hors cas nominal ;
- aucune correction si aucune violation initiale ;
- aucun retry LLM, aucun re-prompt, aucun appel à blender_client ;
- maximum une passe corrective : pas de boucle ;
- subprocess bpy unique qui fait modification .blend + re-render preview.

Paramètres canoniques (Key_Light, Fill_Light, Camera) repris à l'identique de
TEMPLATE_PRODUCT_RENDER dans blender_templates.py. Toute divergence doit
être manuelle et documentée.

Le module n'importe PAS blender_client.py (cf. consigne H.4.8) — il
duplique le minimum nécessaire à l'écriture du script bpy de re-render
(setup EEVEE + world background + résolution).
"""
from __future__ import annotations

import subprocess
from pathlib import Path


# ---------------------------------------------------------------------------
# Paramètres canoniques (synchronisés avec TEMPLATE_PRODUCT_RENDER)
# ---------------------------------------------------------------------------
# Toute modification ici doit refléter une modification simultanée dans
# blender_templates.py TEMPLATE_PRODUCT_RENDER, et inversement. Les deux
# blocs sont volontairement dupliqués pour éviter d'importer un template
# rendu sous forme de string Python depuis ce module.
# ---------------------------------------------------------------------------

CANONICAL_KEY_LIGHT = {
    "location": (0.8, -0.6, 1.2),
    "energy": 200.0,
    "size": 1.2,
    "rotation_euler": (0.7, 0.3, 0.6),
}

CANONICAL_FILL_LIGHT = {
    "location": (-0.8, -0.4, 0.8),
    "energy": 60.0,
    "size": 1.0,
    "rotation_euler": (0.9, -0.3, -0.4),
}

# H.4.8.1 — Cadrage packshot recalibré.
#
# Divergence VOLONTAIRE par rapport à TEMPLATE_PRODUCT_RENDER (blender_templates.py),
# qui conserve location=(0.5, -0.7, 0.35), rotation=(1.25, 0, 0.6), lens=80 pour
# rester compatible avec les patterns du scaffold prompt-only existant (et ne pas
# réintroduire de régression type H.4.6/H.4.6b côté génération LLM).
#
# Cette divergence est limitée à la PASSE CORRECTIVE post-exécution :
# - distance reculée ~1.8x sur le même rayon visuel (recul d'environ 0.6m du sujet)
# - focale ramenée de 80mm (télé) à 50mm (focale packshot standard)
# - pitch légèrement augmenté pour viser le milieu vertical du sujet (z≈0.175)
#   au lieu de l'origine (z=0), ce qui recentre la bouteille + le socle dans le frame.
#
# Hauteur visible théorique à 1.53m avec lens=50 et sensor 24mm : ~0.73m.
# Un sujet contractuel de hauteur ~0.35m occupe donc ~48 % du frame → produit
# entier lisible, socle visible mais non dominant.
CANONICAL_CAMERA = {
    "location": (0.85, -1.20, 0.60),
    "rotation_euler": (1.30, 0.0, 0.6),
    "lens": 50,
}

# Re-rendu canonique (aligné sur _render_preview de blender_client.py)
CORRECTION_RENDER_RESOLUTION_X = 512
CORRECTION_RENDER_RESOLUTION_Y = 512
CORRECTION_WORLD_BG_RGBA = (0.05, 0.05, 0.05, 1.0)

# Nom de l'objet sujet qui sert de gate pour appliquer la correction
REQUIRED_SUBJECT_NAME = "Product_Subject"

# Noms canoniques des correctives — utilisés dans corrections_applied
CORRECTION_ADD_KEY_LIGHT    = "add_key_light"
CORRECTION_ADD_FILL_LIGHT   = "add_fill_light"
CORRECTION_REMOVE_SUN       = "remove_sun"
CORRECTION_REFRAME_CAMERA   = "reframe_camera"
CORRECTION_RERENDER_PREVIEW = "rerender_preview"


# ---------------------------------------------------------------------------
# Planification — fonction PURE
# ---------------------------------------------------------------------------

def plan_corrections(
    template_name: str | None,
    object_names: list[str] | None,
    initial_violations: list[str] | None,
) -> dict:
    """
    Détermine la liste des corrections à appliquer pour atteindre le contrat
    runtime product_render. Pure : pas d'I/O. Testable.

    Retourne :
      {
        "applicable": bool,
        "reason": str | None,   # raison du skip si applicable=False
        "corrections": list[str],
      }

    Règles V0 :
      - template_name != "product_render"     → applicable=False
      - object_names absent                    → applicable=False
      - Product_Subject absent                 → applicable=False
      - aucune violation initiale              → applicable=True mais
        corrections=[CORRECTION_REFRAME_CAMERA, CORRECTION_RERENDER_PREVIEW]
        si on souhaite ré-appliquer le cadrage canonique, ou liste vide
        si rien à faire. En V0 : si pas de violation, on ne fait rien.
    """
    if template_name != "product_render":
        return {"applicable": False, "reason": "template_not_product_render", "corrections": []}
    if not object_names:
        return {"applicable": False, "reason": "no_object_names", "corrections": []}
    if not isinstance(object_names, list):
        return {"applicable": False, "reason": "invalid_object_names", "corrections": []}

    name_set = set(object_names)
    if REQUIRED_SUBJECT_NAME not in name_set:
        return {"applicable": False, "reason": "no_product_subject", "corrections": []}

    corrections: list[str] = []
    if "Sun" in name_set:
        corrections.append(CORRECTION_REMOVE_SUN)
    if "Key_Light" not in name_set:
        corrections.append(CORRECTION_ADD_KEY_LIGHT)
    if "Fill_Light" not in name_set:
        corrections.append(CORRECTION_ADD_FILL_LIGHT)

    # En V0 : on n'applique le cadrage caméra canonique et le re-rendu que
    # si au moins une modification structurelle a été planifiée. Sinon, la
    # scène est déjà conforme et on évite un subprocess Blender coûteux.
    if corrections:
        corrections.append(CORRECTION_REFRAME_CAMERA)
        corrections.append(CORRECTION_RERENDER_PREVIEW)

    if not corrections:
        return {"applicable": True, "reason": "no_corrections_needed", "corrections": []}

    return {"applicable": True, "reason": None, "corrections": corrections}


# ---------------------------------------------------------------------------
# Génération du script bpy de correction — fonction PURE
# ---------------------------------------------------------------------------

def build_correction_script(
    blend_path: str,
    render_path: str | None,
    corrections: list[str],
) -> str:
    """
    Construit le texte du script bpy de correction.

    Le script est exécuté avec :
      blender --background <blend_path> --python <generated_script_path>

    Aucun import LLM, aucun appel réseau. Modification déterministe
    de la scène + sauvegarde en place + (optionnel) re-rendu preview.

    Pure : pas d'I/O. Testable.
    """
    has_remove_sun  = CORRECTION_REMOVE_SUN in corrections
    has_add_key     = CORRECTION_ADD_KEY_LIGHT in corrections
    has_add_fill    = CORRECTION_ADD_FILL_LIGHT in corrections
    has_reframe     = CORRECTION_REFRAME_CAMERA in corrections
    has_rerender    = CORRECTION_RERENDER_PREVIEW in corrections and render_path

    lines: list[str] = []
    lines.append("import bpy")
    lines.append("scene = bpy.context.scene")

    # 1. Neutralisation Sun (avant ajout Key_Light pour éviter une éventuelle
    #    collision de nom).
    if has_remove_sun:
        lines += [
            'sun_obj = bpy.data.objects.get("Sun")',
            'if sun_obj is not None:',
            '    bpy.data.objects.remove(sun_obj, do_unlink=True)',
        ]

    # 2. Ajout Key_Light (canonique product_render AREA)
    if has_add_key:
        kl = CANONICAL_KEY_LIGHT
        lines += [
            'if bpy.data.objects.get("Key_Light") is None:',
            f'    bpy.ops.object.light_add(type="AREA", location={kl["location"]})',
            '    _kl = bpy.context.object',
            '    _kl.name = "Key_Light"',
            f'    _kl.data.energy = {kl["energy"]}',
            f'    _kl.data.size = {kl["size"]}',
            f'    _kl.rotation_euler = {kl["rotation_euler"]}',
        ]

    # 3. Ajout Fill_Light (canonique product_render AREA)
    if has_add_fill:
        fl = CANONICAL_FILL_LIGHT
        lines += [
            'if bpy.data.objects.get("Fill_Light") is None:',
            f'    bpy.ops.object.light_add(type="AREA", location={fl["location"]})',
            '    _fl = bpy.context.object',
            '    _fl.name = "Fill_Light"',
            f'    _fl.data.energy = {fl["energy"]}',
            f'    _fl.data.size = {fl["size"]}',
            f'    _fl.rotation_euler = {fl["rotation_euler"]}',
        ]

    # 4. Cadrage caméra canonique (position / rotation / lens fixés du scaffold).
    #    On NE déduit PAS le cadrage du bbox du sujet : V0 reste déterministe.
    if has_reframe:
        cam = CANONICAL_CAMERA
        lines += [
            '_cam = bpy.data.objects.get("Camera")',
            'if _cam is not None and _cam.type == "CAMERA":',
            f'    _cam.location = {cam["location"]}',
            f'    _cam.rotation_euler = {cam["rotation_euler"]}',
            '    if _cam.data is not None:',
            f'        _cam.data.lens = {cam["lens"]}',
            '    scene.camera = _cam',
        ]

    # 5. Sauvegarde du .blend corrigé en place
    lines.append(f'bpy.ops.wm.save_as_mainfile(filepath=r"{blend_path}")')

    # 6. Re-rendu preview.png (EEVEE, 512x512, fond gris sombre — aligné
    #    sur _render_preview de blender_client.py).
    if has_rerender:
        bg = CORRECTION_WORLD_BG_RGBA
        lines += [
            f'scene.render.resolution_x = {CORRECTION_RENDER_RESOLUTION_X}',
            f'scene.render.resolution_y = {CORRECTION_RENDER_RESOLUTION_Y}',
            'scene.render.resolution_percentage = 100',
            'scene.render.image_settings.file_format = "PNG"',
            f'scene.render.filepath = r"{render_path}"',
            '_eevee_engines = ["BLENDER_EEVEE", "BLENDER_EEVEE_NEXT"]',
            '_avail = scene.render.bl_rna.properties["engine"].enum_items.keys()',
            'for _eng in _eevee_engines:',
            '    if _eng in _avail:',
            '        scene.render.engine = _eng',
            '        break',
            '_world = scene.world',
            'if _world is None:',
            '    _world = bpy.data.worlds.new("World")',
            '    scene.world = _world',
            '_world.use_nodes = True',
            '_bg = _world.node_tree.nodes.get("Background")',
            'if _bg is not None:',
            f'    _bg.inputs[0].default_value = {bg}',
            '    _bg.inputs[1].default_value = 1.0',
            'bpy.ops.render.render(write_still=True)',
        ]

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Orchestrateur — lance le subprocess Blender
# ---------------------------------------------------------------------------

def apply_corrections(
    exe: str | None,
    blend_path: str,
    output_dir: str,
    render_path: str | None,
    template_name: str | None,
    object_names: list[str] | None,
    initial_violations: list[str] | None,
    timeout: int,
) -> dict:
    """
    Applique les corrections déterministes au .blend si applicable.

    Retourne toujours un dict :
      {
        "status": "applied | skipped | not_available | error",
        "corrections_applied": list[str],
        "reason": str | None,
        "stderr": str | None,         # tronqué si non vide
      }

    Ne lève jamais d'exception. Best-effort : un échec subprocess ne casse
    pas le pipeline ; le rapport reflète l'erreur.
    """
    plan = plan_corrections(template_name, object_names, initial_violations)

    if not plan["applicable"]:
        return {
            "status": "skipped",
            "corrections_applied": [],
            "reason": plan["reason"],
            "stderr": None,
        }

    if not plan["corrections"]:
        return {
            "status": "skipped",
            "corrections_applied": [],
            "reason": "no_corrections_needed",
            "stderr": None,
        }

    if not exe:
        return {
            "status": "not_available",
            "corrections_applied": [],
            "reason": "blender_exe_not_found",
            "stderr": None,
        }

    if not Path(blend_path).exists():
        return {
            "status": "error",
            "corrections_applied": [],
            "reason": "blend_path_not_found",
            "stderr": None,
        }

    script_text = build_correction_script(blend_path, render_path, plan["corrections"])
    script_path = Path(output_dir) / "_correction_scene.py"

    try:
        script_path.write_text(script_text, encoding="utf-8")
    except Exception as exc:
        return {
            "status": "error",
            "corrections_applied": [],
            "reason": f"script_write_failed: {exc}",
            "stderr": None,
        }

    try:
        proc = subprocess.run(
            [exe, "--background", blend_path, "--python", str(script_path)],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        _safe_unlink(script_path)
        return {
            "status": "error",
            "corrections_applied": [],
            "reason": "timeout",
            "stderr": None,
        }
    except Exception as exc:
        _safe_unlink(script_path)
        return {
            "status": "error",
            "corrections_applied": [],
            "reason": f"subprocess_exception: {exc}",
            "stderr": None,
        }

    _safe_unlink(script_path)

    if proc.returncode != 0:
        return {
            "status": "error",
            "corrections_applied": [],
            "reason": f"returncode={proc.returncode}",
            "stderr": (proc.stderr or "")[:2000] or None,
        }

    return {
        "status": "applied",
        "corrections_applied": plan["corrections"],
        "reason": None,
        "stderr": None,
    }


def _safe_unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except Exception:
        pass
