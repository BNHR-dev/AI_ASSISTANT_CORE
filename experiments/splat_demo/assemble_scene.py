# bpy script — HOST Blender, addon enabled (no --factory-startup).
# Opens the imported-splat .blend, appends the contract objects (Pedestal +
# Product_Subject) from a canonical pipeline run, keeps them at world origin with
# the canonical camera, and transforms the splat environment around them per
# layout.json. Renders the composite + a product-only mask pass + dumps the
# framing_raw data the contract computation needs.
#
# Usage:
#   blender --background <splat_import.blend is opened from layout> \
#     --python assemble_scene.py -- --layout layout.json --out <dir> [--preview]
#
# NOTE: run on the .blend given by layout["splat_blend"]; the CLI passes it as
# the file argument. --preview renders at 512 for fast iteration.

import json
import math
import os
import sys

import bpy
from mathutils import Vector

argv = sys.argv[sys.argv.index("--") + 1 :]


def arg(name: str, default=None):
    return argv[argv.index(name) + 1] if name in argv else default


LAYOUT = json.loads(open(arg("--layout", "layout.json"), encoding="utf-8").read())
OUT_DIR = arg("--out", ".")
PREVIEW = "--preview" in argv
os.makedirs(OUT_DIR, exist_ok=True)

scene = bpy.context.scene

# --- 1. Append the contract objects from the pipeline run ------------------
run_blend = LAYOUT["source_run_blend"]
with bpy.data.libraries.load(run_blend, link=False) as (data_from, data_to):
    wanted = [n for n in data_from.objects if n in ("Pedestal", "Product_Subject", "Product_Cap")]
    data_to.objects = wanted
for obj in data_to.objects:
    if obj is not None:
        scene.collection.objects.link(obj)
print("APPENDED:", [o.name for o in data_to.objects if o])

# --- 2. Camera + lights (canonical, from layout) ----------------------------
cam_cfg = LAYOUT["camera"]
cam_data = bpy.data.cameras.new("Camera")
cam_data.lens = cam_cfg["lens_mm"]
cam = bpy.data.objects.new("Camera", cam_data)
scene.collection.objects.link(cam)
cam.location = cam_cfg["location"]
cam.rotation_euler = cam_cfg["rotation_euler"]
scene.camera = cam

for name, loc, size in (("Key_Light", (0.8, -0.6, 1.2), 1.2), ("Fill_Light", (-0.8, -0.4, 0.8), 1.0)):
    cfg = LAYOUT["lights"]["key" if name == "Key_Light" else "fill"]
    ldata = bpy.data.lights.new(name, type="AREA")
    ldata.energy = cfg["energy"]
    ldata.color = cfg["color"]
    ldata.size = size
    light = bpy.data.objects.new(name, ldata)
    scene.collection.objects.link(light)
    light.location = loc
    aim = Vector((0.0, 0.0, 0.15)) - light.location
    light.rotation_euler = aim.to_track_quat("-Z", "Y").to_euler()

# --- 3. Transform the splat environment around the product ------------------
splat = bpy.data.objects[LAYOUT["splat_object"]]
tr = LAYOUT["splat_transform"]
splat.rotation_euler = [math.radians(a) for a in tr["rotation_euler_deg"]]
splat.location = tr["location"]
splat.scale = (tr["scale"],) * 3
# Splat modifiers: demo-proven settings (Update Mode=0), then refresh for our camera.
for m in splat.modifiers:
    if m.type == "NODES" and m.node_group:
        for item in m.node_group.interface.items_tree:
            if item.item_type == "SOCKET" and item.in_out == "INPUT":
                if item.name == "Update Mode":
                    m[item.identifier] = 0
                if item.name == "Use Active Camera":
                    m[item.identifier] = False
bpy.ops.sna.dgs_render_update_enabled_3dgs_objects_6d7f4()

# --- 4. Render settings (NO preview_fidelity block — it would stomp the world)
for eng in ("BLENDER_EEVEE", "BLENDER_EEVEE_NEXT"):
    if eng in scene.render.bl_rna.properties["engine"].enum_items.keys():
        scene.render.engine = eng
        break
res = 512 if PREVIEW else LAYOUT["render"]["resolution"]
scene.render.resolution_x = res
scene.render.resolution_y = res
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = "PNG"

# --- 5. Composite render -----------------------------------------------------
scene.render.filepath = os.path.join(OUT_DIR, "render.png")
bpy.ops.render.render(write_still=True)
print("RENDER_DONE")

# --- 6. Product-only mask pass (perceptual bbox source) ----------------------
# film_transparent + hide everything except the product: alpha = its pixel footprint.
visible_backup = {}
for obj in scene.objects:
    if obj.type in ("MESH", "LIGHT") and obj.name != "Product_Subject":
        visible_backup[obj.name] = obj.hide_render
        obj.hide_render = True
scene.render.film_transparent = True
scene.render.filepath = os.path.join(OUT_DIR, "mask.png")
bpy.ops.render.render(write_still=True)
for name, was_hidden in visible_backup.items():
    bpy.data.objects[name].hide_render = was_hidden
scene.render.film_transparent = False
print("MASK_DONE")

# --- 7. framing_raw dump (same fields as the validator's inspect script) -----
subject = bpy.data.objects["Product_Subject"]
depsgraph = bpy.context.evaluated_depsgraph_get()
subject_eval = subject.evaluated_get(depsgraph)
corners = [list(subject_eval.matrix_world @ Vector(c)) for c in subject_eval.bound_box]
framing_raw = {
    "view_matrix": [list(row) for row in cam.matrix_world.inverted()],
    "lens": cam_data.lens,
    "sensor_width": cam_data.sensor_width,
    "sensor_height": cam_data.sensor_height,
    "sensor_fit": cam_data.sensor_fit,
    "shift_x": cam_data.shift_x,
    "shift_y": cam_data.shift_y,
    "res_x": res,
    "res_y": res,
    "pixel_aspect_x": scene.render.pixel_aspect_x,
    "pixel_aspect_y": scene.render.pixel_aspect_y,
    "subject_corners": corners,
}
with open(os.path.join(OUT_DIR, "framing_raw.json"), "w", encoding="utf-8") as fh:
    json.dump(framing_raw, fh, indent=2)
print("FRAMING_RAW_DONE")
