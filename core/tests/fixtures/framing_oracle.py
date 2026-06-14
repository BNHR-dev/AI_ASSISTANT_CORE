"""
Fixture oracle (exécutée DANS Blender) pour valider le module pur
framing_contract contre world_to_camera_view.

Crée la caméra canonique, projette une liste de points monde via l'API
Blender officielle et écrit le résultat JSON. Le test (hors Blender) compare
ces (u, v, z) à framing_contract.project_point.

Usage : blender --background --factory-startup --python framing_oracle.py -- <out.json>
"""
import bpy
import json
import sys
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Vector

out_path = sys.argv[sys.argv.index("--") + 1:][0]

LOCATION = (0.85, -1.20, 0.60)
EULER = (1.30, 0.0, 0.6)
LENS = 50.0

scene = bpy.context.scene
scene.render.resolution_x = 512
scene.render.resolution_y = 512
scene.render.pixel_aspect_x = 1.0
scene.render.pixel_aspect_y = 1.0

bpy.ops.object.camera_add(location=LOCATION)
cam = bpy.context.object
cam.rotation_euler = EULER
cam.data.lens = LENS
cam.data.sensor_width = 36.0
cam.data.sensor_fit = "AUTO"
scene.camera = cam

# matrix_world est paresseux : forcer la mise à jour après pose/lens.
bpy.context.view_layer.update()

points = [
    (0.0, 0.0, 0.0),
    (0.04, 0.0, 0.30),
    (-0.04, 0.04, 0.20),
    (0.0, 0.0, 0.50),
    (0.20, -0.10, 0.10),
    (-0.08, 0.06, 0.04),
]
wcv = []
for p in points:
    co = world_to_camera_view(scene, cam, Vector(p))
    wcv.append([co.x, co.y, co.z])

mw_inv = [list(row) for row in cam.matrix_world.inverted()]

with open(out_path, "w", encoding="utf-8") as f:
    json.dump({"location": LOCATION, "euler": EULER, "lens": LENS,
               "points": points, "wcv": wcv, "view_matrix": mw_inv}, f)
