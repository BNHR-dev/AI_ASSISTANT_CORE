"""
H.6.11 — builder de fixture DÉTERMINISTE pour la preuve optique de fidélité.

Exécuté côté Blender :
    blender --background --factory-startup --python preview_fidelity_scene.py -- <mode> <out.blend>

Modes :
  - glass  : sujet en verre (transmission 1.0, IOR 1.45) devant un motif
             damier fortement contrasté ET ÉMISSIF (auto-éclairé, donc visible
             quelle que soit la lumière de scène). Socle MAT.
  - opaque : sujet identique mais MAT opaque (transmission 0). Baseline : le
             motif ne doit PAS être visible à travers le sujet.
  - metal  : sujet chromé (metallic 1.0) + environnement structuré clair/sombre
             (deux plans émissifs) pour révéler la réflexion. Socle mat.

Objets nommés selon le contrat product_render (Backdrop_Plane, Pedestal,
Product_Subject, Camera, Key_Light, Fill_Light) afin que le runtime corrector
applique sa normalisation + re-rendu (le chemin réellement livré).

Ne dépend d'aucune ressource externe : tout est procédural.
"""
import sys

import bpy

mode = sys.argv[-2]
out_path = sys.argv[-1]

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene


def _new_principled(name):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    return mat, bsdf


def _set_transmission(bsdf, value):
    inp = bsdf.inputs.get("Transmission Weight") or bsdf.inputs.get("Transmission")
    if inp is not None:
        inp.default_value = value


# --- Backdrop : motif damier ÉMISSIF fortement contrasté ---------------------
bpy.ops.mesh.primitive_plane_add(size=3.0, location=(0.0, 1.1, 0.35))
backdrop = bpy.context.object
backdrop.name = "Backdrop_Plane"
backdrop.rotation_euler = (1.2, 0.0, 0.0)
bd_mat = bpy.data.materials.new("Backdrop_Checker")
bd_mat.use_nodes = True
_bd_nt = bd_mat.node_tree
for _n in list(_bd_nt.nodes):
    _bd_nt.nodes.remove(_n)
_out = _bd_nt.nodes.new("ShaderNodeOutputMaterial")
_emit = _bd_nt.nodes.new("ShaderNodeEmission")
_chk = _bd_nt.nodes.new("ShaderNodeTexChecker")
_chk.inputs["Color1"].default_value = (1.0, 1.0, 1.0, 1.0)
_chk.inputs["Color2"].default_value = (0.0, 0.0, 0.0, 1.0)
_chk.inputs["Scale"].default_value = 14.0
_emit.inputs["Strength"].default_value = 6.0
_bd_nt.links.new(_chk.outputs["Color"], _emit.inputs["Color"])
_bd_nt.links.new(_emit.outputs["Emission"], _out.inputs["Surface"])
backdrop.data.materials.append(bd_mat)

# --- Pedestal MAT ------------------------------------------------------------
bpy.ops.mesh.primitive_cylinder_add(radius=0.28, depth=0.08, location=(0.0, 0.0, 0.04))
pedestal = bpy.context.object
pedestal.name = "Pedestal"
ped_mat, ped_bsdf = _new_principled("Pedestal_Material")
ped_bsdf.inputs["Base Color"].default_value = (0.35, 0.35, 0.35, 1.0)
ped_bsdf.inputs["Roughness"].default_value = 0.9
ped_bsdf.inputs["Metallic"].default_value = 0.0
_set_transmission(ped_bsdf, 0.0)
pedestal.data.materials.append(ped_mat)

# --- Product_Subject : sphère, matériau selon le mode ------------------------
bpy.ops.mesh.primitive_uv_sphere_add(radius=0.20, location=(0.0, 0.0, 0.32))
bpy.ops.object.shade_smooth()
product = bpy.context.object
product.name = "Product_Subject"
prod_mat, prod_bsdf = _new_principled("Product_Material")
if mode == "glass":
    prod_bsdf.inputs["Base Color"].default_value = (0.9, 0.95, 1.0, 1.0)
    prod_bsdf.inputs["Roughness"].default_value = 0.0
    prod_bsdf.inputs["Metallic"].default_value = 0.0
    prod_bsdf.inputs["IOR"].default_value = 1.45
    _set_transmission(prod_bsdf, 1.0)
elif mode == "metal":
    prod_bsdf.inputs["Base Color"].default_value = (0.95, 0.95, 0.95, 1.0)
    prod_bsdf.inputs["Roughness"].default_value = 0.06
    prod_bsdf.inputs["Metallic"].default_value = 1.0
    _set_transmission(prod_bsdf, 0.0)
else:  # opaque (baseline mat)
    prod_bsdf.inputs["Base Color"].default_value = (0.6, 0.6, 0.6, 1.0)
    prod_bsdf.inputs["Roughness"].default_value = 0.85
    prod_bsdf.inputs["Metallic"].default_value = 0.0
    _set_transmission(prod_bsdf, 0.0)
product.data.materials.append(prod_mat)

# --- Environnement structuré clair/sombre pour le mode metal -----------------
# (fait partie de la FIXTURE, pas de l'environnement livré : permis explicitement.)
# Grand damier émissif placé CÔTÉ CAMÉRA (-Y), donc réfléchi sur la face visible
# de la sphère chromée. Hors champ caméra (derrière elle) : n'occulte rien.
if mode == "metal":
    bpy.ops.mesh.primitive_plane_add(size=7.0, location=(0.0, -2.6, 0.6))
    _env = bpy.context.object
    _env.rotation_euler = (1.5708, 0.0, 0.0)  # face vers +Y (vers la sphère)
    _em = bpy.data.materials.new("Env_Checker")
    _em.use_nodes = True
    _ent = _em.node_tree
    for _n in list(_ent.nodes):
        _ent.nodes.remove(_n)
    _eo = _ent.nodes.new("ShaderNodeOutputMaterial")
    _ee = _ent.nodes.new("ShaderNodeEmission")
    _ec = _ent.nodes.new("ShaderNodeTexChecker")
    _ec.inputs["Color1"].default_value = (1.0, 1.0, 1.0, 1.0)
    _ec.inputs["Color2"].default_value = (0.0, 0.0, 0.0, 1.0)
    _ec.inputs["Scale"].default_value = 10.0
    _ee.inputs["Strength"].default_value = 7.0
    _ent.links.new(_ec.outputs["Color"], _ee.inputs["Color"])
    _ent.links.new(_ee.outputs["Emission"], _eo.inputs["Surface"])
    _env.data.materials.append(_em)

# --- Caméra (sera normalisée par le corrector) -------------------------------
bpy.ops.object.camera_add(location=(0.85, -1.2, 0.6))
cam = bpy.context.object
cam.name = "Camera"
cam.rotation_euler = (1.3, 0.0, 0.6)
cam.data.lens = 50
scene.camera = cam

# --- Lumières (cibles de normalize_lighting) ---------------------------------
bpy.ops.object.light_add(type="AREA", location=(0.8, -0.6, 1.2))
kl = bpy.context.object
kl.name = "Key_Light"
kl.data.energy = 25.0
bpy.ops.object.light_add(type="AREA", location=(-0.8, -0.4, 0.8))
fl = bpy.context.object
fl.name = "Fill_Light"
fl.data.energy = 10.0

bpy.ops.wm.save_as_mainfile(filepath=out_path)
print("FIXTURE_BUILT", mode, out_path)
