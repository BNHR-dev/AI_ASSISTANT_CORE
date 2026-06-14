"""
H.6.11 preview_fidelity_v1 — politique PARTAGÉE de fidélité matière pour le
rendu preview Blender (EEVEE Next, vérifié Blender 5.1.1).

Source UNIQUE de la politique, importée par les DEUX chemins qui peuvent écrire
preview.png :
  - app.clients.blender_client._render_preview   (chemin générique / legacy)
  - app.engine.blender_runtime_corrector         (dernier writer product_render)

Le corrector n'importe PAS blender_client (consigne H.4.8). Ce module est neutre
(aucun import bpy, aucune I/O : il ne fait qu'émettre des lignes de script bpy
sous forme de chaînes) — il garantit que les deux chemins appliquent EXACTEMENT
la même politique, sans dépendance croisée et sans divergence possible.

Politique (APIs confirmées par introspection Blender 5.1.1) :
  1. ray tracing scène sous garde hasattr (absent en EEVEE legacy <=4.1 → no-op) ;
  2. environnement procédural directionnel borné : Geometry.Incoming (direction
     du rayon en espace MONDE) → Z → ColorRamp 0.03→0.12. Donne au métal une
     falloff lisible à réfléchir, stable et indépendante de la caméra/écran ;
  3. réfraction activée UNIQUEMENT sur matériaux à transmission > 0 (input exact
     5.1.1 'Transmission Weight', repli 'Transmission'), flag primaire
     use_raytrace_refraction, repli conditionnel use_screen_refraction.

Ne touche NI caméra, NI lumières, NI exposition, NI Fast GI, NI ombres, NI
résolution, NI échantillonnage. Modifications EN MÉMOIRE dans le subprocess de
rendu : aucun save_as_mainfile ici (scene.blend n'est jamais réécrit).
"""
from __future__ import annotations

# Bornes du gradient neutre/discret. Volontairement basses pour servir de source
# de réflexion sans éclairer la scène ni écraser l'exposition H.6.9.
PREVIEW_ENV_LOW: tuple[float, float, float, float] = (0.03, 0.03, 0.03, 1.0)
PREVIEW_ENV_HIGH: tuple[float, float, float, float] = (0.12, 0.12, 0.12, 1.0)


def preview_fidelity_script_lines() -> list[str]:
    """
    Lignes de script bpy (sans newline final) implémentant la politique de
    fidélité preview H.6.11.

    Auto-suffisant : ne dépend d'aucune variable locale préexistante (tous les
    identifiants sont préfixés `_pf_`). Valide dans tout script qui a déjà
    importé `bpy`.
    """
    return [
        "# --- H.6.11 preview_fidelity_v1 (source unique: blender_preview_fidelity) ---",
        "_pf_scene = bpy.context.scene",
        "# 1. ray tracing scene (verre + metal), garde defensive EEVEE legacy",
        'if hasattr(_pf_scene.eevee, "use_raytracing"):',
        "    _pf_scene.eevee.use_raytracing = True",
        "# 2. environnement procedural directionnel borne (Geometry.Incoming world-space)",
        "_pf_world = _pf_scene.world",
        "if _pf_world is None:",
        '    _pf_world = bpy.data.worlds.new("World")',
        "    _pf_scene.world = _pf_world",
        "_pf_world.use_nodes = True",
        "_pf_nt = _pf_world.node_tree",
        '_pf_bg = _pf_nt.nodes.get("Background")',
        "if _pf_bg is not None:",
        '    _pf_geo = _pf_nt.nodes.new("ShaderNodeNewGeometry")',
        '    _pf_sep = _pf_nt.nodes.new("ShaderNodeSeparateXYZ")',
        '    _pf_mr = _pf_nt.nodes.new("ShaderNodeMapRange")',
        '    _pf_ramp = _pf_nt.nodes.new("ShaderNodeValToRGB")',
        '    _pf_mr.inputs["From Min"].default_value = -1.0',
        '    _pf_mr.inputs["From Max"].default_value = 1.0',
        '    _pf_mr.inputs["To Min"].default_value = 0.0',
        '    _pf_mr.inputs["To Max"].default_value = 1.0',
        "    _pf_ramp.color_ramp.elements[0].position = 0.0",
        f"    _pf_ramp.color_ramp.elements[0].color = {PREVIEW_ENV_LOW}",
        "    _pf_ramp.color_ramp.elements[1].position = 1.0",
        f"    _pf_ramp.color_ramp.elements[1].color = {PREVIEW_ENV_HIGH}",
        '    _pf_nt.links.new(_pf_geo.outputs["Incoming"], _pf_sep.inputs["Vector"])',
        '    _pf_nt.links.new(_pf_sep.outputs["Z"], _pf_mr.inputs["Value"])',
        '    _pf_nt.links.new(_pf_mr.outputs["Result"], _pf_ramp.inputs["Fac"])',
        '    _pf_nt.links.new(_pf_ramp.outputs["Color"], _pf_bg.inputs["Color"])',
        '    _pf_bg.inputs["Strength"].default_value = 1.0',
        "# 3. refraction transitoire sur materiaux transmissifs uniquement",
        "for _pf_mat in bpy.data.materials:",
        "    if not _pf_mat.use_nodes:",
        "        continue",
        '    _pf_bsdf = next((n for n in _pf_mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)',
        "    if _pf_bsdf is None:",
        "        continue",
        '    _pf_tw = _pf_bsdf.inputs.get("Transmission Weight") or _pf_bsdf.inputs.get("Transmission")',
        "    if _pf_tw is not None and _pf_tw.default_value > 0.0:",
        '        if hasattr(_pf_mat, "use_raytrace_refraction"):',
        "            _pf_mat.use_raytrace_refraction = True",
        '        elif hasattr(_pf_mat, "use_screen_refraction"):',
        "            _pf_mat.use_screen_refraction = True",
    ]


def preview_fidelity_script_block() -> str:
    """Bloc texte (lignes jointes par newline) prêt à insérer dans un script."""
    return "\n".join(preview_fidelity_script_lines())
