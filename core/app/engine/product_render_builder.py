"""
H.5.1 — Product Render Builder déterministe.

Génère un script bpy product_render complet et déterministe à partir d'un
ProductRenderIntent (IR V0). Pur Python : pas d'I/O, pas de subprocess,
pas d'appel LLM, pas d'import bpy au niveau module (seul le script
généré utilise bpy à l'exécution Blender).

Cadré par l'ADR [[16_H5_PRODUCT_RENDER_IR_CADRAGE]] (Décision 11) :
- le LLM décide QUOI via l'IR ;
- le builder décide COMMENT : noms contractuels, primitives, matériaux,
  caméra canonique, lumières canoniques, cleanup, save.

Garanties par construction :
- noms contractuels présents : Backdrop_Plane, Pedestal, Product_Subject,
  Camera, Key_Light, Fill_Light
- pas de Sun créé
- pas d'`import_scene.*`
- pas de chemin externe
- pas de `bpy.data.meshes.new` sans `from_pydata`
- cleanup `select_all + delete` toujours présent
- caméra active toujours assignée
- script lisible par AST guard H.4.7 sans violation
- runtime_contract H.4.8 satisfait (rien à corriger)

Réutilise les constantes `CANONICAL_*` de `blender_runtime_corrector.py` :
SINGLE SOURCE OF TRUTH pour caméra + Key_Light + Fill_Light. Toute
modification de cadrage canonique reste dans le corrector ; le builder
les importe.
"""
from __future__ import annotations

from app.engine.blender_runtime_corrector import (
    CANONICAL_CAMERA,
    CANONICAL_FILL_LIGHT,
    CANONICAL_KEY_LIGHT,
)
from app.engine.product_render_ir import (
    ProductRenderIntent,
    resolve_color,
)


# ---------------------------------------------------------------------------
# Constantes scaffold canoniques (NON exposées dans l'IR V0)
# ---------------------------------------------------------------------------

# Pedestal canonique (cylindre court). Toujours présent en V0.
CANONICAL_PEDESTAL = {
    "primitive": "primitive_cylinder_add",
    "radius": 0.10,
    "depth": 0.04,
    "location": (0.0, 0.0, 0.02),  # base au sol, top à z=0.04
    "color_rgba": (0.30, 0.30, 0.30, 1.0),  # gris foncé neutre
    "material_profile": "matte",
    "name": "Pedestal",
}

# Backdrop canonique (plan large incliné derrière le sujet).
# Seule la couleur est exposée dans l'IR V0 ; forme, taille, inclinaison fixes.
CANONICAL_BACKDROP = {
    "primitive": "primitive_plane_add",
    "size": 4.0,
    "location": (0.0, 1.0, 0.0),
    "rotation_euler": (1.2, 0.0, 0.0),  # incliné vers la caméra
    "scale": (1.5, 1.5, 1.0),
    "material_profile": "matte",
    "name": "Backdrop_Plane",
}

# Le top du pedestal est à z=0.04. Le subject est posé dessus, sa base à z=0.04.
PEDESTAL_TOP_Z = (
    CANONICAL_PEDESTAL["location"][2] + CANONICAL_PEDESTAL["depth"] / 2.0
)


# ---------------------------------------------------------------------------
# Mapping subject.kind → primitive bpy + dimensions canoniques
# ---------------------------------------------------------------------------
# Chaque kind décrit une géométrie déterministe. Le subject est toujours
# posé sur le pedestal (sa base à z = PEDESTAL_TOP_Z), centré sur l'origine
# en (x, y) = (0, 0).
#
# Choix dimensionnels V0 : produits de ~10-25 cm de hauteur, cohérents
# avec le cadrage canonique CANONICAL_CAMERA (lens=50, distance ~1.55m,
# hauteur visible ~0.73m → 14-35 % d'occupation verticale).

SUBJECT_GEOMETRY: dict[str, dict] = {
    "bottle": {
        # cylindre haut fin (parfum, sirop, etc.)
        "primitive": "primitive_cylinder_add",
        "radius": 0.04,
        "depth": 0.22,
    },
    "jar": {
        # cylindre court large (pot de crème, etc.)
        "primitive": "primitive_cylinder_add",
        "radius": 0.06,
        "depth": 0.10,
    },
    "box": {
        # cube régulier (packaging, etc.)
        "primitive": "primitive_cube_add",
        "size": 0.12,
    },
    "tube": {
        # cylindre très fin et long (tube cosmétique, etc.)
        "primitive": "primitive_cylinder_add",
        "radius": 0.025,
        "depth": 0.18,
    },
    "cylinder": {
        # cylindre générique (canette, etc.)
        "primitive": "primitive_cylinder_add",
        "radius": 0.05,
        "depth": 0.15,
    },
    "sphere": {
        # sphère (boule, fruit, etc.)
        "primitive": "primitive_uv_sphere_add",
        "radius": 0.06,
    },
}


def _subject_location(kind: str) -> tuple[float, float, float]:
    """
    Calcule la position du subject pour qu'il repose sur le pedestal.
    Pour les cylindres et cubes, le centre est à PEDESTAL_TOP_Z + half_height.
    Pour la sphère, le centre est à PEDESTAL_TOP_Z + radius.

    Pure : pas d'I/O.
    """
    geom = SUBJECT_GEOMETRY[kind]
    if "depth" in geom:
        half_height = geom["depth"] / 2.0
    elif "size" in geom:
        half_height = geom["size"] / 2.0
    else:
        # sphère
        half_height = geom["radius"]
    return (0.0, 0.0, PEDESTAL_TOP_Z + half_height)


# ---------------------------------------------------------------------------
# Mapping subject.material → Principled BSDF params
# ---------------------------------------------------------------------------
# Table-driven : chaque profil renvoie un dict de paramètres bpy à appliquer
# sur les inputs du nœud Principled BSDF. Le builder écrit ces paramètres
# de manière déterministe dans le script.
#
# Note : on évite d'utiliser des inputs avancés (subsurface, sheen, coat) qui
# varient selon les versions Blender. Reste sur base_color, roughness, metallic,
# transmission, ior — disponibles depuis Blender 2.8+.

MATERIAL_PROFILES: dict[str, dict] = {
    "matte": {
        "roughness": 0.90,
        "metallic": 0.0,
        "transmission": 0.0,
        "ior": 1.45,
    },
    "glossy": {
        "roughness": 0.20,
        "metallic": 0.0,
        "transmission": 0.0,
        "ior": 1.45,
    },
    "glass": {
        "roughness": 0.05,
        "metallic": 0.0,
        "transmission": 1.0,
        "ior": 1.45,
    },
    "metallic": {
        "roughness": 0.20,
        "metallic": 1.0,
        "transmission": 0.0,
        "ior": 1.45,
    },
}


# ---------------------------------------------------------------------------
# Génération du script bpy — fonction publique
# ---------------------------------------------------------------------------

def build_product_render_scene_script(intent: ProductRenderIntent) -> str:
    """
    Génère un script bpy product_render déterministe à partir de l'IR V0.

    Le script retourné est une string Python prête à être :
    - écrite dans `scene.py`
    - enveloppée par `_inject_output_paths()` de `blender_client.py`
      (pour l'injection de `OUTPUT_BLEND_PATH` et le try/finally pipeline)
    - exécutée par `blender --background --python scene.py`

    Le script :
    - importe bpy
    - fait le cleanup canonique (select_all + delete)
    - crée les collections SCENE + PROPS
    - crée Backdrop_Plane (canonique, couleur depuis IR)
    - crée Pedestal (canonique, gris foncé)
    - crée Product_Subject (primitive depuis IR.subject.kind, matériau depuis IR.subject.material, couleur depuis IR.subject.color)
    - crée Camera (CANONICAL_CAMERA, scene.camera assignée)
    - crée Key_Light (CANONICAL_KEY_LIGHT, AREA)
    - crée Fill_Light (CANONICAL_FILL_LIGHT, AREA)
    - sauvegarde via OUTPUT_BLEND_PATH (placeholder injecté par le pipeline)

    Pure : pas d'I/O, pas d'appel LLM, pas d'import externe.
    """
    subject_color_rgba = resolve_color(intent.subject.color)
    backdrop_color_rgba = resolve_color(intent.backdrop.color)
    subject_geom = SUBJECT_GEOMETRY[intent.subject.kind]
    subject_loc = _subject_location(intent.subject.kind)
    subject_material_params = MATERIAL_PROFILES[intent.subject.material]

    lines: list[str] = []

    # En-tête + cleanup canonique
    lines += [
        "import bpy",
        "",
        "# --- H.5.1 deterministic product_render builder ---",
        f"# schema_version = {intent.schema_version!r}",
        f"# subject.kind = {intent.subject.kind!r}",
        f"# subject.color = {intent.subject.color!r}",
        f"# subject.material = {intent.subject.material!r}",
        f"# backdrop.color = {intent.backdrop.color!r}",
        "",
        "# Cleanup canonique scène par défaut",
        "bpy.ops.object.select_all(action='SELECT')",
        "bpy.ops.object.delete()",
        "",
        "# Unités métriques",
        "bpy.context.scene.unit_settings.system = 'METRIC'",
        "bpy.context.scene.unit_settings.scale_length = 1.0",
        "",
        "# Collections SCENE + PROPS (cohérent avec TEMPLATE_PRODUCT_RENDER)",
        "scene_col = bpy.data.collections.new('SCENE')",
        "bpy.context.scene.collection.children.link(scene_col)",
        "props_col = bpy.data.collections.new('PROPS')",
        "bpy.context.scene.collection.children.link(props_col)",
        "",
        "def _link_to(obj, col):",
        "    if obj.name in bpy.context.scene.collection.objects:",
        "        bpy.context.scene.collection.objects.unlink(obj)",
        "    col.objects.link(obj)",
        "",
        "def _make_principled_material(name, base_color_rgba, roughness, metallic, transmission, ior):",
        "    mat = bpy.data.materials.new(name=name)",
        "    mat.use_nodes = True",
        "    nodes = mat.node_tree.nodes",
        "    bsdf = nodes.get('Principled BSDF')",
        "    if bsdf is not None:",
        "        bsdf.inputs['Base Color'].default_value = base_color_rgba",
        "        bsdf.inputs['Roughness'].default_value = roughness",
        "        bsdf.inputs['Metallic'].default_value = metallic",
        "        if 'Transmission' in bsdf.inputs:",
        "            bsdf.inputs['Transmission'].default_value = transmission",
        "        elif 'Transmission Weight' in bsdf.inputs:",
        "            bsdf.inputs['Transmission Weight'].default_value = transmission",
        "        if 'IOR' in bsdf.inputs:",
        "            bsdf.inputs['IOR'].default_value = ior",
        "    return mat",
        "",
    ]

    # Backdrop canonique
    bd = CANONICAL_BACKDROP
    bd_params = MATERIAL_PROFILES[bd["material_profile"]]
    lines += [
        "# --- Backdrop_Plane (canonique) ---",
        f"bpy.ops.mesh.{bd['primitive']}(size={bd['size']}, location={bd['location']})",
        "backdrop = bpy.context.object",
        f"backdrop.name = {bd['name']!r}",
        f"backdrop.rotation_euler = {bd['rotation_euler']}",
        f"backdrop.scale = {bd['scale']}",
        "backdrop_mat = _make_principled_material(",
        "    'Backdrop_Material',",
        f"    {backdrop_color_rgba},",
        f"    {bd_params['roughness']}, {bd_params['metallic']}, "
        f"{bd_params['transmission']}, {bd_params['ior']},",
        ")",
        "backdrop.data.materials.append(backdrop_mat)",
        "_link_to(backdrop, scene_col)",
        "",
    ]

    # Pedestal canonique
    pd = CANONICAL_PEDESTAL
    pd_params = MATERIAL_PROFILES[pd["material_profile"]]
    lines += [
        "# --- Pedestal (canonique) ---",
        f"bpy.ops.mesh.{pd['primitive']}(radius={pd['radius']}, "
        f"depth={pd['depth']}, location={pd['location']})",
        "pedestal = bpy.context.object",
        f"pedestal.name = {pd['name']!r}",
        "pedestal_mat = _make_principled_material(",
        "    'Pedestal_Material',",
        f"    {pd['color_rgba']},",
        f"    {pd_params['roughness']}, {pd_params['metallic']}, "
        f"{pd_params['transmission']}, {pd_params['ior']},",
        ")",
        "pedestal.data.materials.append(pedestal_mat)",
        "_link_to(pedestal, scene_col)",
        "",
    ]

    # Product_Subject depuis l'IR
    primitive = subject_geom["primitive"]
    if primitive == "primitive_cylinder_add":
        primitive_call = (
            f"bpy.ops.mesh.primitive_cylinder_add("
            f"radius={subject_geom['radius']}, depth={subject_geom['depth']}, "
            f"location={subject_loc})"
        )
    elif primitive == "primitive_cube_add":
        primitive_call = (
            f"bpy.ops.mesh.primitive_cube_add("
            f"size={subject_geom['size']}, location={subject_loc})"
        )
    elif primitive == "primitive_uv_sphere_add":
        primitive_call = (
            f"bpy.ops.mesh.primitive_uv_sphere_add("
            f"radius={subject_geom['radius']}, location={subject_loc})"
        )
    else:
        # Garde-fou : tous les kinds sont mappés ; si ce code est atteint,
        # c'est un bug interne à SUBJECT_GEOMETRY.
        raise ValueError(f"Unknown primitive for kind {intent.subject.kind}: {primitive}")

    lines += [
        f"# --- Product_Subject (IR.subject.kind = {intent.subject.kind!r}) ---",
        primitive_call,
        "product = bpy.context.object",
        "product.name = 'Product_Subject'",
        "product_mat = _make_principled_material(",
        "    'Product_Material',",
        f"    {subject_color_rgba},",
        f"    {subject_material_params['roughness']}, "
        f"{subject_material_params['metallic']}, "
        f"{subject_material_params['transmission']}, "
        f"{subject_material_params['ior']},",
        ")",
        "product.data.materials.append(product_mat)",
        "_link_to(product, scene_col)",
        "",
    ]

    # Camera canonique (CANONICAL_CAMERA H.4.8.1)
    cam = CANONICAL_CAMERA
    lines += [
        "# --- Camera (CANONICAL_CAMERA H.4.8.1, single source of truth) ---",
        f"bpy.ops.object.camera_add(location={cam['location']})",
        "cam = bpy.context.object",
        "cam.name = 'Camera'",
        f"cam.rotation_euler = {cam['rotation_euler']}",
        f"cam.data.lens = {cam['lens']}",
        "bpy.context.scene.camera = cam",
        "_link_to(cam, scene_col)",
        "",
    ]

    # Key_Light canonique (CANONICAL_KEY_LIGHT H.4.8)
    kl = CANONICAL_KEY_LIGHT
    lines += [
        "# --- Key_Light (CANONICAL_KEY_LIGHT H.4.8, single source of truth) ---",
        f"bpy.ops.object.light_add(type='AREA', location={kl['location']})",
        "key_light = bpy.context.object",
        "key_light.name = 'Key_Light'",
        f"key_light.data.energy = {kl['energy']}",
        f"key_light.data.size = {kl['size']}",
        f"key_light.rotation_euler = {kl['rotation_euler']}",
        "_link_to(key_light, scene_col)",
        "",
    ]

    # Fill_Light canonique (CANONICAL_FILL_LIGHT H.4.8)
    fl = CANONICAL_FILL_LIGHT
    lines += [
        "# --- Fill_Light (CANONICAL_FILL_LIGHT H.4.8, single source of truth) ---",
        f"bpy.ops.object.light_add(type='AREA', location={fl['location']})",
        "fill_light = bpy.context.object",
        "fill_light.name = 'Fill_Light'",
        f"fill_light.data.energy = {fl['energy']}",
        f"fill_light.data.size = {fl['size']}",
        f"fill_light.rotation_euler = {fl['rotation_euler']}",
        "_link_to(fill_light, scene_col)",
        "",
    ]

    # Sauvegarde via le placeholder OUTPUT_BLEND_PATH injecté par le pipeline
    lines += [
        "# --- Sauvegarde gérée par le pipeline (OUTPUT_BLEND_PATH injecté) ---",
        "bpy.ops.wm.save_as_mainfile(filepath=OUTPUT_BLEND_PATH)",
        "",
    ]

    return "\n".join(lines)
