"""
Blender Blocking Templates — scaffolds bpy pour scènes de blocking.

Chaque template est un scaffold Python bpy stable que le LLM adapte et complète.
Le template garantit les invariants de blocking (sol, sujet, caméra, lumière,
nommage) même si le LLM modifie les dimensions, matériaux ou objets secondaires.

Le LLM ne doit PAS modifier :
- la structure caméra active et son assignation à bpy.context.scene.camera
- la lumière principale
- les noms des objets structurants (Floor_, Camera, Key_Light)
- la logique de sauvegarde (gérée par le pipeline via OUTPUT_BLEND_PATH)
- la compatibilité Blender 4.x

Usage :
    select_template(message) → str (scaffold) | None (fallback comportement actuel)
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# Mots-clés de sélection de templates
# ---------------------------------------------------------------------------

_INTERIOR_KEYWORDS = (
    "intérieur", "interieur", "interior",
    "bureau", "office",
    "room", "pièce", "piece",
    "couloir", "corridor", "hallway",
    "salon", "living",
    "cuisine", "kitchen",
    "chambre", "bedroom",
    "scène intérieure", "scene intérieure",
    "indoor",
    "salle", "hall",
)


# ---------------------------------------------------------------------------
# Template interior_space
# ---------------------------------------------------------------------------
# Scaffold d'une scène de blocking intérieure.
# Structure garantie :
#   - collection SCENE, collection PROPS
#   - sol plat (Floor_Plane)
#   - 3 murs (Wall_Back, Wall_Left, Wall_Right)
#   - sujet principal proxy au centre (Main_Subject)
#   - caméra active de type medium shot
#   - lumière clé SUN (Key_Light)
#   - unités métriques, échelle humaine (hauteur plafond ~3m)
#
# Le LLM peut adapter :
#   - les dimensions des murs et du sol
#   - les matériaux (couleur, roughness)
#   - les objets secondaires dans la collection PROPS
#   - la position et le nom du sujet principal
#   - l'ajout d'objets de décor (table, lampe, fenêtre…)
#
# Le LLM NE doit PAS modifier :
#   - la caméra active et son assignation
#   - la lumière Key_Light
#   - la sauvegarde (gérée par OUTPUT_BLEND_PATH via le pipeline)
#   - la compatibilité Blender 4.x
# ---------------------------------------------------------------------------

TEMPLATE_INTERIOR_SPACE = """\
import bpy

# -- Nettoyage scène par défaut --
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete()

# -- Unités métriques --
bpy.context.scene.unit_settings.system = 'METRIC'
bpy.context.scene.unit_settings.scale_length = 1.0

# -- Collections --
scene_col = bpy.data.collections.new("SCENE")
bpy.context.scene.collection.children.link(scene_col)
props_col = bpy.data.collections.new("PROPS")
bpy.context.scene.collection.children.link(props_col)

def link_to(obj, col):
    bpy.context.scene.collection.objects.unlink(obj) if obj.name in bpy.context.scene.collection.objects else None
    col.objects.link(obj)

# -- Sol --
bpy.ops.mesh.primitive_plane_add(size=10, location=(0, 0, 0))
floor = bpy.context.object
floor.name = "Floor_Plane"
floor.scale = (1, 1, 1)
link_to(floor, scene_col)

# -- Mur arrière --
bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 5, 1.5))
wall_back = bpy.context.object
wall_back.name = "Wall_Back"
wall_back.scale = (5, 0.15, 1.5)
link_to(wall_back, scene_col)

# -- Mur gauche --
bpy.ops.mesh.primitive_cube_add(size=1, location=(-5, 0, 1.5))
wall_left = bpy.context.object
wall_left.name = "Wall_Left"
wall_left.scale = (0.15, 5, 1.5)
link_to(wall_left, scene_col)

# -- Mur droit --
bpy.ops.mesh.primitive_cube_add(size=1, location=(5, 0, 1.5))
wall_right = bpy.context.object
wall_right.name = "Wall_Right"
wall_right.scale = (0.15, 5, 1.5)
link_to(wall_right, scene_col)

# -- Sujet principal proxy (capsule = silhouette humaine simplifiée) --
# Le LLM peut adapter la forme, les dimensions et le nom selon la demande.
bpy.ops.mesh.primitive_cylinder_add(radius=0.3, depth=1.8, location=(0, 0, 0.9))
main_subject = bpy.context.object
main_subject.name = "Main_Subject"
link_to(main_subject, scene_col)

# -- Objet focal secondaire (optionnel, adaptable par le LLM) --
bpy.ops.mesh.primitive_cube_add(size=0.6, location=(1.5, 1.5, 0.3))
focal_obj = bpy.context.object
focal_obj.name = "Focal_Object"
link_to(focal_obj, props_col)

# -- Caméra active — medium shot orienté vers le sujet --
# NE PAS modifier la logique de caméra active.
bpy.ops.object.camera_add(location=(0, -6, 2.2))
cam = bpy.context.object
cam.name = "Camera"
cam.rotation_euler = (1.1, 0, 0)
bpy.context.scene.camera = cam
cam.data.lens = 35
link_to(cam, scene_col)

# -- Lumière clé --
# NE PAS supprimer ni renommer Key_Light.
bpy.ops.object.light_add(type='SUN', location=(4, -4, 6))
key_light = bpy.context.object
key_light.name = "Key_Light"
key_light.data.energy = 3.0
key_light.rotation_euler = (0.8, 0.2, 0.5)
link_to(key_light, scene_col)

# -- Sauvegarde gérée par le pipeline (OUTPUT_BLEND_PATH injecté automatiquement) --
bpy.ops.wm.save_as_mainfile(filepath=OUTPUT_BLEND_PATH)
"""


# ---------------------------------------------------------------------------
# Sélection de template (par message brut — comportement historique)
# ---------------------------------------------------------------------------

def select_template(message: str) -> str | None:
    """
    Retourne le scaffold bpy correspondant au type de scène détecté dans le message.
    Retourne None si aucun template ne correspond → fallback vers le comportement actuel.

    Détection déterministe par mots-clés uniquement.
    """
    msg_lower = message.lower()

    if any(kw in msg_lower for kw in _INTERIOR_KEYWORDS):
        return TEMPLATE_INTERIOR_SPACE

    return None


def get_template_name(message: str) -> str | None:
    """
    Retourne le nom du template sélectionné, ou None.
    Utile pour les tests et les traces.
    """
    msg_lower = message.lower()
    if any(kw in msg_lower for kw in _INTERIOR_KEYWORDS):
        return "interior_space"
    return None


# ---------------------------------------------------------------------------
# Sélection de template par creative_intent — H.4.1
# ---------------------------------------------------------------------------
# Sujets ArtisticIntent qui mappent vers interior_space.
# Reste conservateur : seuls les sujets clairement "scène intérieure".
_INTERIOR_INTENT_SUBJECTS = (
    "laboratoire", "salle", "bureau", "office",
    "room", "salon", "cuisine", "chambre",
    "couloir", "corridor", "hall", "hangar",
)


def _intent_field(intent: object, name: str) -> object:
    """Accès tolérant : ArtisticIntent (attr) ou dict (key). None si absent."""
    if intent is None:
        return None
    if isinstance(intent, dict):
        return intent.get(name)
    return getattr(intent, name, None)


def select_template_from_intent(intent: object) -> str | None:
    """
    Retourne le scaffold bpy à partir d'un ArtisticIntent ou son dict équivalent.
    Retourne None si aucun template ne correspond → l'appelant peut alors
    retomber sur select_template(message) pour préserver la rétrocompat.

    Règle V1 (conservatrice) :
      medium == "3d_scene" ET subject_main contient un mot-clé d'intérieur
        → interior_space
      sinon → None
    """
    if intent is None:
        return None

    medium = _intent_field(intent, "medium")
    if medium != "3d_scene":
        return None

    subject_main = _intent_field(intent, "subject_main") or ""
    if not isinstance(subject_main, str):
        return None

    subject_lower = subject_main.lower()
    if any(kw in subject_lower for kw in _INTERIOR_INTENT_SUBJECTS):
        return TEMPLATE_INTERIOR_SPACE

    return None


def get_template_name_from_intent(intent: object) -> str | None:
    """Nom du template sélectionné via l'intent, ou None."""
    scaffold = select_template_from_intent(intent)
    if scaffold is TEMPLATE_INTERIOR_SPACE:
        return "interior_space"
    return None
