# bpy template — executed by headless Blender INSIDE the backend container, on a copy
# of a base run's scene.blend. The dataset driver replaces the fidelity marker line
# below with the pipeline's shared preview-fidelity block before execution, so mutated
# renders and pipeline renders share the exact same render policy.
#
# Env contract: JEPA_VARIANT (variant name), JEPA_OUT (output dir, must exist).
# Never saves the .blend — mutations live only in this process; only renders + a
# post-mutation object inventory (objects.json) are written.

import json
import math
import os

import bpy
from mathutils import Vector

VARIANT = os.environ["JEPA_VARIANT"]
OUT_DIR = os.environ["JEPA_OUT"]

scene = bpy.context.scene
# Some pipeline .blends carry a Camera object without the active-camera pointer set.
cam = scene.camera
if cam is None:
    cam = next((o for o in scene.objects if o.type == "CAMERA"), None)
    if cam is None:
        raise SystemExit("no camera object in scene — cannot render")
    scene.camera = cam


def orbit_and_scale(theta_deg: float, k: float) -> None:
    """World-Z orbit around the origin + radial distance scaling.

    Adding theta to euler.z left-composes Rz(theta) onto the camera rotation, which
    matches rotating its location about the world Z axis: aim at the (origin-centred)
    subject is preserved.
    """
    th = math.radians(theta_deg)
    x, y, z = cam.location
    cam.location = (
        k * (x * math.cos(th) - y * math.sin(th)),
        k * (x * math.sin(th) + y * math.cos(th)),
        k * z,
    )
    cam.rotation_euler.z += th


if VARIANT == "conform_j1":
    orbit_and_scale(4.0, 0.97)
elif VARIANT == "conform_j2":
    orbit_and_scale(-4.0, 1.03)
elif VARIANT == "conform_j3":
    orbit_and_scale(2.5, 1.01)
elif VARIANT == "deg_nolight":
    # Canonical name first; else the strongest light plays the key-light role.
    key = bpy.data.objects.get("Key_Light")
    if key is None:
        lights = [o for o in scene.objects if o.type == "LIGHT"]
        key = max(lights, key=lambda o: o.data.energy, default=None)
    if key is None:
        raise SystemExit("no light in scene — cannot inject the missing-light defect")
    bpy.data.objects.remove(key, do_unlink=True)
elif VARIANT == "deg_framing":
    orbit_and_scale(0.0, 2.5)
    cam.location.x += 0.5
    cam.location.z += 0.25
elif VARIANT == "deg_intruder":
    bpy.ops.mesh.primitive_cube_add(size=0.15, location=(0.28, -0.02, 0.075))
    cube = bpy.context.object
    cube.name = "Intruder_Cube"
    mat = bpy.data.materials.new("Intruder_Mat")
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf is not None:
        bsdf.inputs["Base Color"].default_value = (0.35, 0.33, 0.30, 1.0)
    cube.data.materials.append(mat)
elif VARIANT == "deg_rimlight":
    light_data = bpy.data.lights.new("Rim_Light", type="AREA")
    light_data.energy = 200.0
    light_data.color = (1.0, 0.05, 0.6)
    light_data.size = 1.0
    rim = bpy.data.objects.new("Rim_Light", light_data)
    scene.collection.objects.link(rim)
    rim.location = (-0.6, 0.5, 0.5)
    aim = Vector((0.0, 0.0, 0.15)) - rim.location
    rim.rotation_euler = aim.to_track_quat("-Z", "Y").to_euler()
else:
    raise SystemExit(f"unknown variant: {VARIANT}")

# Post-mutation inventory — feeds evaluate_runtime_contract() back in the driver.
with open(os.path.join(OUT_DIR, "objects.json"), "w", encoding="utf-8") as fh:
    json.dump({"object_names": [o.name for o in scene.objects]}, fh)

### PREVIEW_FIDELITY_BLOCK ###

# Same render policy as the pipeline preview: EEVEE picked robustly, fixed 512x512.
_eevee_engines = ["BLENDER_EEVEE", "BLENDER_EEVEE_NEXT"]
_available = scene.render.bl_rna.properties["engine"].enum_items.keys()
for _eng in _eevee_engines:
    if _eng in _available:
        scene.render.engine = _eng
        break
scene.render.resolution_x = 512
scene.render.resolution_y = 512
scene.render.resolution_percentage = 100
scene.render.filepath = os.path.join(OUT_DIR, "preview.png")
bpy.ops.render.render(write_still=True)
