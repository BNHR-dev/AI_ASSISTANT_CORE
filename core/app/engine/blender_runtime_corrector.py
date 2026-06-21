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

import os
import subprocess
from pathlib import Path

from app.clients.blender_sandbox import build_sandbox_plan, PROFILE_RENDER
from app.engine.blender_preview_fidelity import preview_fidelity_script_lines
from app.engine.hero_framing import (
    HERO_FRAMING_REPORT_FILENAME,
)

# Répertoire des modules purs `framing_contract` / `hero_framing`, injecté dans
# le script bpy généré pour qu'il IMPORTE la même source que les tests (zéro
# réimplémentation de la métrique/politique). Les deux modules sont stdlib-only
# à l'import → chargeables dans le python embarqué de Blender.
_ENGINE_DIR = str(Path(__file__).resolve().parent)


# ---------------------------------------------------------------------------
# Paramètres canoniques (synchronisés avec TEMPLATE_PRODUCT_RENDER)
# ---------------------------------------------------------------------------
# Toute modification ici doit refléter une modification simultanée dans
# blender_templates.py TEMPLATE_PRODUCT_RENDER, et inversement. Les deux
# blocs sont volontairement dupliqués pour éviter d'importer un template
# rendu sous forme de string Python depuis ce module.
# ---------------------------------------------------------------------------

# H.6.9 — hero_framing_v1 : énergies recalibrées (key 200→25 W, fill 60→10 W,
# ratio key:fill 2.5:1). À 200 W la key surexposait le backdrop : un fond
# neutral_gray (albédo 0.5) sortait à ~240/255 de luminance moyenne sur les
# 3 smokes de l'audit 2026-06-10 (decor_dominates 3/3). Calibration validée
# par smokes 2026-06-11 : à 80 W les backdrops clairs restaient cramés
# (médiane fond 226-255) ; à 25 W la médiane fond périphérique tombe dans la
# cible [80, 210] pour les albédos 0.2-0.7. Divergence VOLONTAIRE avec
# TEMPLATE_PRODUCT_RENDER (blender_templates.py), qui garde 200/60 pour ne
# pas perturber le scaffold prompt-only existant.
CANONICAL_KEY_LIGHT = {
    "location": (0.8, -0.6, 1.2),
    "energy": 25.0,
    "size": 1.2,
    "rotation_euler": (0.7, 0.3, 0.6),
}

CANONICAL_FILL_LIGHT = {
    "location": (-0.8, -0.4, 0.8),
    "energy": 10.0,
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
#
# Correctif H.6.9 : le capteur effectif du rendu est 36 mm (défaut Blender,
# sensor_fit AUTO, rendu carré), pas 24 mm — la hauteur visible réelle à
# 1.53 m est ~1.10 m et l'occupation des sujets actuels ~15-28 %. Le contrôle
# d'occupation borné est appliqué dans build_correction_script via les
# constantes de app.engine.hero_framing (réf. : hero_distance_factor).
CANONICAL_CAMERA = {
    "location": (0.85, -1.20, 0.60),
    "rotation_euler": (1.30, 0.0, 0.6),
    "lens": 50,
}

# Re-rendu canonique (résolution alignée sur _render_preview de blender_client.py).
# H.6.11 : l'ancien fond plat CORRECTION_WORLD_BG_RGBA est remplacé par la
# politique de fidélité partagée (blender_preview_fidelity), source unique.
CORRECTION_RENDER_RESOLUTION_X = 512
CORRECTION_RENDER_RESOLUTION_Y = 512

# Nom de l'objet sujet qui sert de gate pour appliquer la correction
REQUIRED_SUBJECT_NAME = "Product_Subject"

# Noms canoniques des correctives — utilisés dans corrections_applied
CORRECTION_ADD_KEY_LIGHT    = "add_key_light"
CORRECTION_ADD_FILL_LIGHT   = "add_fill_light"
CORRECTION_REMOVE_SUN       = "remove_sun"
CORRECTION_REFRAME_CAMERA   = "reframe_camera"
CORRECTION_RERENDER_PREVIEW = "rerender_preview"

# H.4.8.2 — Normalisation passive (contrat déjà OK, on remet quand même les
# paramètres canoniques pour éviter une composition LLM mauvaise).
CORRECTION_NORMALIZE_CAMERA   = "normalize_camera"
CORRECTION_NORMALIZE_LIGHTING = "normalize_lighting"

# Ensemble minimal d'objets structurels requis pour qu'une normalisation
# passive ait du sens. Si l'un d'eux manque, on retombe dans le chemin
# correctif H.4.8 (ou on skippe).
NORMALIZATION_MINIMUM_OBJECTS: set[str] = {
    "Backdrop_Plane",
    "Pedestal",
    "Product_Subject",
    "Camera",
}


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
      - template_name != "product_render"   → applicable=False
      - object_names absent                  → applicable=False
      - Product_Subject absent               → applicable=False

      Si une violation contractuelle est détectée (Sun présent, Key_Light
      ou Fill_Light manquant) → chemin CORRECTIF H.4.8 :
        [remove_sun?, add_key_light?, add_fill_light?,
         reframe_camera, rerender_preview]

      Si aucune violation contractuelle MAIS les 4 objets structurels
      minimum sont présents → chemin NORMALISATION H.4.8.2 :
        [normalize_lighting, normalize_camera, rerender_preview]
      Motivé par : le contrat structurel ne garantit pas une composition
      lisible. La normalisation réapplique les paramètres canoniques
      (caméra + lumières) sans modifier les objets, pour stabiliser le
      cadrage packshot.

      Si aucune violation MAIS minimum incomplet → skipped avec raison.
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

    # --- Chemin CORRECTIF H.4.8 ---------------------------------------------
    active_corrections: list[str] = []
    if "Sun" in name_set:
        active_corrections.append(CORRECTION_REMOVE_SUN)
    if "Key_Light" not in name_set:
        active_corrections.append(CORRECTION_ADD_KEY_LIGHT)
    if "Fill_Light" not in name_set:
        active_corrections.append(CORRECTION_ADD_FILL_LIGHT)

    if active_corrections:
        active_corrections.append(CORRECTION_REFRAME_CAMERA)
        active_corrections.append(CORRECTION_RERENDER_PREVIEW)
        return {"applicable": True, "reason": None, "corrections": active_corrections}

    # --- Chemin NORMALISATION H.4.8.2 ---------------------------------------
    # Aucune violation contractuelle : si les 4 objets structurels minimum
    # sont présents, on applique quand même la normalisation canonique pour
    # garantir un cadrage packshot stable, indépendamment de ce que le LLM
    # a produit pour la caméra et l'éclairage.
    if NORMALIZATION_MINIMUM_OBJECTS.issubset(name_set):
        return {
            "applicable": True,
            "reason": None,
            "corrections": [
                CORRECTION_NORMALIZE_LIGHTING,
                CORRECTION_NORMALIZE_CAMERA,
                CORRECTION_RERENDER_PREVIEW,
            ],
        }

    # Cas pathologique : pas de violation contractuelle mais l'un des objets
    # structurels minimum manque (peut arriver si la spec runtime diverge
    # du minimum de normalisation). On ne tente pas de deviner.
    return {
        "applicable": True,
        "reason": "minimum_normalization_context_missing",
        "corrections": [],
    }


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
    has_remove_sun       = CORRECTION_REMOVE_SUN in corrections
    has_add_key          = CORRECTION_ADD_KEY_LIGHT in corrections
    has_add_fill         = CORRECTION_ADD_FILL_LIGHT in corrections
    has_reframe          = CORRECTION_REFRAME_CAMERA in corrections
    has_normalize_cam    = CORRECTION_NORMALIZE_CAMERA in corrections
    has_normalize_light  = CORRECTION_NORMALIZE_LIGHTING in corrections
    has_rerender         = CORRECTION_RERENDER_PREVIEW in corrections and render_path

    # H.4.8.2 — caméra canonique appliquée que ce soit en correctif ou en
    # normalisation : les deux écrivent exactement le même code bpy.
    apply_canonical_camera = has_reframe or has_normalize_cam

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

    # 3.b H.4.8.2 — Normalisation lighting (Key_Light + Fill_Light déjà présents).
    #     On réapplique les paramètres canoniques sans tenter de re-créer les
    #     objets. La conversion en AREA est guarded : on ne touche `data.size`
    #     que si le type est déjà AREA, pour éviter de planter sur SUN/POINT.
    if has_normalize_light:
        kl = CANONICAL_KEY_LIGHT
        fl = CANONICAL_FILL_LIGHT
        lines += [
            '_nkl = bpy.data.objects.get("Key_Light")',
            'if _nkl is not None and _nkl.type == "LIGHT":',
            f'    _nkl.location = {kl["location"]}',
            f'    _nkl.data.energy = {kl["energy"]}',
            f'    _nkl.rotation_euler = {kl["rotation_euler"]}',
            '    if _nkl.data.type == "AREA":',
            f'        _nkl.data.size = {kl["size"]}',
            '_nfl = bpy.data.objects.get("Fill_Light")',
            'if _nfl is not None and _nfl.type == "LIGHT":',
            f'    _nfl.location = {fl["location"]}',
            f'    _nfl.data.energy = {fl["energy"]}',
            f'    _nfl.rotation_euler = {fl["rotation_euler"]}',
            '    if _nfl.data.type == "AREA":',
            f'        _nfl.data.size = {fl["size"]}',
        ]

    # 4. Cadrage caméra canonique (position / rotation / lens fixés du scaffold).
    #    On NE déduit PAS le cadrage du bbox du sujet : V0 reste déterministe.
    #    Appliqué à l'identique pour `reframe_camera` (correctif H.4.8) et
    #    `normalize_camera` (normalisation H.4.8.2).
    if apply_canonical_camera:
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

        # V1.1a hero_framing — contrôle d'occupation NDC unifiée.
        # Le script N'INLINE PLUS la formule : il importe les modules purs
        # framing_contract (mesure NDC) et hero_framing (politique) par chemin,
        # donc exécute exactement la source que les tests exercent (anti-
        # duplication). Mesure → déclenchement → déplacement → RE-MESURE NDC,
        # tout sur la même occupation. Avant/après loggués dans hero_framing.json.
        hero_report_path = str(
            Path(blend_path).with_name(HERO_FRAMING_REPORT_FILENAME)
        )
        lines += [
            '# --- V1.1a hero_framing : occupation NDC unifiee (framing_contract) ---',
            'import json as _hf_json',
            'import sys as _hf_sys',
            f'_hf_sys.path.insert(0, r"{_ENGINE_DIR}")',
            'import framing_contract as _hf_fc',
            'import hero_framing as _hf_pol',
            'from mathutils import Vector as _HFVector',
            '_hf = {"phase": "hero_framing_v1_1a", "applied": False, "reason": None,',
            '       "occupancy_before": None, "occupancy_after": None,',
            '       "target_occupancy": None, "occupancy_residual": None,',
            '       "clamped": False, "target_reached": None,',
            '       "in_contract_band_after": None,',
            '       "distance_before": None, "distance_after": None,',
            '       "subject_height": None, "factor_requested": None, "factor": None}',
            '_hf_subj = bpy.data.objects.get("Product_Subject")',
            '_hf_cam = bpy.data.objects.get("Camera")',
            'if _hf_cam is None or _hf_cam.type != "CAMERA" or _hf_subj is None:',
            '    _hf["reason"] = "camera_or_subject_missing"',
            'else:',
            '    _hf_cd = _hf_cam.data',
            '    def _hf_measure():',
            '        _c = [_hf_subj.matrix_world @ _HFVector(_v) for _v in _hf_subj.bound_box]',
            '        _vm = tuple(tuple(_r) for _r in _hf_cam.matrix_world.inverted())',
            '        _hw, _hh = _hf_fc.half_extents_at_unit_depth(',
            '            _hf_cd.lens, _hf_cd.sensor_width, _hf_cd.sensor_height, _hf_cd.sensor_fit,',
            '            scene.render.resolution_x, scene.render.resolution_y,',
            '            scene.render.pixel_aspect_x, scene.render.pixel_aspect_y)',
            '        _occ = _hf_fc.occupancy_from_scene(_vm, {"half_w": _hw, "half_h": _hh},',
            '                                           [tuple(_v[:]) for _v in _c])',
            '        _zmin = min(_v.z for _v in _c); _zmax = max(_v.z for _v in _c)',
            '        _ctr = _HFVector((sum(_v.x for _v in _c) / 8.0,',
            '                          sum(_v.y for _v in _c) / 8.0, (_zmin + _zmax) / 2.0))',
            '        return _occ, _ctr, (_zmax - _zmin)',
            '    _hf_occ0, _hf_ctr, _hf_height = _hf_measure()',
            '    _hf_d0 = (_hf_cam.location - _hf_ctr).length',
            '    _hf_factor = _hf_pol.hero_distance_factor(_hf_occ0)',
            '    _hf["occupancy_before"] = round(_hf_occ0, 4)',
            '    _hf["distance_before"] = round(_hf_d0, 4)',
            '    _hf["subject_height"] = round(_hf_height, 4)',
            '    _hf["factor_requested"] = round(_hf_pol.requested_factor(_hf_occ0), 4)',
            '    _hf["factor"] = round(_hf_factor, 4)',
            '    _hf["clamped"] = bool(_hf_pol.is_clamped(_hf_occ0))',
            '    _hf_occ1 = None',
            '    if _hf_factor == 1.0 or _hf_pol.target_occupancy_for(_hf_occ0) is None:',
            '        _hf_occ1 = _hf_occ0',
            '        _hf["reason"] = "occupancy_within_bounds"',
            '        _hf["distance_after"] = _hf["distance_before"]',
            '    else:',
            '        _hf_d1 = _hf_pol.clamp_distance(_hf_d0 * _hf_factor)',
            '        _hf_dir = _hf_cam.location - _hf_ctr',
            '        if _hf_dir.length > 0:',
            '            _hf_cam.location = _hf_ctr + _hf_dir.normalized() * _hf_d1',
            '            bpy.context.view_layer.update()',
            '            _hf_occ1, _, _ = _hf_measure()',
            '            _hf["applied"] = True',
            '            _hf["reason"] = "occupancy_out_of_bounds"',
            '            _hf["distance_after"] = round(_hf_d1, 4)',
            '        else:',
            '            _hf["reason"] = "degenerate_camera_position"',
            '    if _hf_occ1 is not None:',
            '        _hf["occupancy_after"] = round(_hf_occ1, 4)',
            '        _hf["in_contract_band_after"] = bool(_hf_fc.in_occupancy_band(_hf_occ1))',
            '        _hf_out = _hf_pol.correction_outcome(_hf_occ0, _hf_occ1)',
            '        _hf["target_occupancy"] = (round(_hf_out["target_occupancy"], 4)',
            '                                   if _hf_out["target_occupancy"] is not None else None)',
            '        _hf["occupancy_residual"] = (round(_hf_out["occupancy_residual"], 4)',
            '                                     if _hf_out["occupancy_residual"] is not None else None)',
            '        _hf["target_reached"] = _hf_out["target_reached"]',
            f'with open(r"{hero_report_path}", "w", encoding="utf-8") as _hf_fh:',
            '    _hf_json.dump(_hf, _hf_fh, indent=2)',
        ]

    # 5. Sauvegarde du .blend corrigé en place
    lines.append(f'bpy.ops.wm.save_as_mainfile(filepath=r"{blend_path}")')

    # 6. Re-rendu preview.png (EEVEE, 512x512) — la politique de fidélité matière
    #    H.6.11 (env directionnel + ray tracing + réfraction) est appliquée via le
    #    bloc partagé, aligné à l'identique avec _render_preview de blender_client.py.
    if has_rerender:
        # Même invariant que _render_preview : Blender résout un render.filepath
        # RELATIF par rapport au .blend (pas au CWD) -> échec silencieux (preview
        # hors cible, returncode 0). On force donc l'absolu. No-op si déjà absolu.
        render_filepath = os.path.abspath(render_path)
        lines += [
            f'scene.render.resolution_x = {CORRECTION_RENDER_RESOLUTION_X}',
            f'scene.render.resolution_y = {CORRECTION_RENDER_RESOLUTION_Y}',
            'scene.render.resolution_percentage = 100',
            'scene.render.image_settings.file_format = "PNG"',
            f'scene.render.filepath = r"{render_filepath}"',
            '_eevee_engines = ["BLENDER_EEVEE", "BLENDER_EEVEE_NEXT"]',
            '_avail = scene.render.bl_rna.properties["engine"].enum_items.keys()',
            'for _eng in _eevee_engines:',
            '    if _eng in _avail:',
            '        scene.render.engine = _eng',
            '        break',
        ]
        # H.6.11 — politique de fidélité matière PARTAGÉE avec _render_preview
        # (blender_preview_fidelity, source unique). Remplace l'ancien fond plat :
        # ray tracing + environnement directionnel borné + réfraction transmissive.
        # N'altère NI caméra, NI lumières, NI exposition (déjà fixées ci-dessus),
        # NI Fast GI/ombres/résolution. Modif en mémoire : pas de save_as_mainfile.
        lines += preview_fidelity_script_lines()
        lines += [
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
        # C1b — flags conservés dans l'argv. C1c — profil render : ce re-rendu
        # corrector produit la preview (GPU EEVEE), confiné sans réseau ni home.
        # Un SandboxError (mode require sans bwrap) remonte à l'except Exception
        # ci-dessous → status error (fail-closed, aucune exécution hors sandbox).
        sandbox_plan = build_sandbox_plan(
            [exe, "--background", "--factory-startup", "--disable-autoexec",
             blend_path, "--python", str(script_path)],
            output_dir=output_dir,
            profile=PROFILE_RENDER,
        )
        print(sandbox_plan.log_line())
        proc = subprocess.run(
            sandbox_plan.argv,
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
