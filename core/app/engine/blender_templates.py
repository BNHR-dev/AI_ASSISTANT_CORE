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

# Mots-clés produit pour le fallback message brut (H.4.2).
# IMPORTANT : ne PAS inclure "studio" seul — trop ambigu (éclairage studio en intérieur).
# On exige des expressions sans ambiguïté vers le packshot / rendu produit.
_PRODUCT_KEYWORDS = (
    "packshot",
    "rendu produit",
    "product render",
    "product_render",
    "mockup produit",
    "mockup product",
    "packaging",
    "bouteille de parfum",
    "bottle of perfume",
    "perfume bottle",
    "flacon de parfum",
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

# ---------------------------------------------------------------------------
# Template product_render — H.4.2
# ---------------------------------------------------------------------------
# Scaffold d'une scène de blocking produit / packshot.
# Structure garantie :
#   - collection SCENE, collection PROPS
#   - backdrop courbe simulé (Backdrop_Plane) — fond neutre
#   - socle / piédestal cylindrique (Pedestal)
#   - objet central proxy (Product_Subject) sur le socle
#   - caméra produit 3/4 (Camera) orientée vers le produit
#   - lumière clé softbox AREA (Key_Light)
#   - unités métriques, échelle produit (sujet ~15 cm)
#
# Le LLM peut adapter :
#   - la forme et les dimensions du produit (bouteille, flacon, cube, sphère…)
#   - les matériaux (verre, métal, plastique)
#   - la couleur / roughness du backdrop et du socle
#   - l'ajout de lumières secondaires (fill, rim) dans PROPS
#
# Le LLM NE doit PAS :
#   - supprimer Camera, Key_Light, Backdrop_Plane, Pedestal, Product_Subject
#   - ajouter de murs Wall_* (réservé à interior_space — éviter la confusion)
#   - changer la logique de sauvegarde via OUTPUT_BLEND_PATH
# ---------------------------------------------------------------------------

TEMPLATE_PRODUCT_RENDER = """\
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

# -- Backdrop neutre (plan large incliné, fond infini simulé) --
bpy.ops.mesh.primitive_plane_add(size=4, location=(0, 1.2, 0))
backdrop = bpy.context.object
backdrop.name = "Backdrop_Plane"
backdrop.rotation_euler = (1.2, 0, 0)
backdrop.scale = (1.5, 1.5, 1)
link_to(backdrop, scene_col)

# -- Socle / piédestal --
bpy.ops.mesh.primitive_cylinder_add(radius=0.25, depth=0.05, location=(0, 0, 0.025))
pedestal = bpy.context.object
pedestal.name = "Pedestal"
link_to(pedestal, scene_col)

# -- Sujet produit proxy (cylindre simulant bouteille / flacon) --
# Le LLM peut adapter la primitive (sphère, cube, capsule) selon le produit demandé.
bpy.ops.mesh.primitive_cylinder_add(radius=0.05, depth=0.18, location=(0, 0, 0.14))
product = bpy.context.object
product.name = "Product_Subject"
link_to(product, scene_col)

# -- Caméra produit 3/4 — orientée vers le sujet --
# NE PAS modifier la logique de caméra active.
bpy.ops.object.camera_add(location=(0.5, -0.7, 0.35))
cam = bpy.context.object
cam.name = "Camera"
cam.rotation_euler = (1.25, 0.0, 0.6)
bpy.context.scene.camera = cam
cam.data.lens = 80
link_to(cam, scene_col)

# -- Lumière clé softbox (AREA) --
# NE PAS supprimer ni renommer Key_Light.
bpy.ops.object.light_add(type='AREA', location=(0.8, -0.6, 1.2))
key_light = bpy.context.object
key_light.name = "Key_Light"
key_light.data.energy = 200.0
key_light.data.size = 1.2
key_light.rotation_euler = (0.7, 0.3, 0.6)
link_to(key_light, scene_col)

# -- Fill light secondaire (optionnel, adaptable par le LLM) --
bpy.ops.object.light_add(type='AREA', location=(-0.8, -0.4, 0.8))
fill_light = bpy.context.object
fill_light.name = "Fill_Light"
fill_light.data.energy = 60.0
fill_light.data.size = 1.0
fill_light.rotation_euler = (0.9, -0.3, -0.4)
link_to(fill_light, props_col)

# -- Sauvegarde gérée par le pipeline (OUTPUT_BLEND_PATH injecté automatiquement) --
bpy.ops.wm.save_as_mainfile(filepath=OUTPUT_BLEND_PATH)
"""


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
    Priorité product_render > interior_space : "packshot produit" reste produit
    même si le mot "produit" n'apparaît pas dans les mots-clés intérieurs.
    """
    msg_lower = message.lower()

    if any(kw in msg_lower for kw in _PRODUCT_KEYWORDS):
        return TEMPLATE_PRODUCT_RENDER

    if any(kw in msg_lower for kw in _INTERIOR_KEYWORDS):
        return TEMPLATE_INTERIOR_SPACE

    return None


def get_template_name(message: str) -> str | None:
    """
    Retourne le nom du template sélectionné, ou None.
    Utile pour les tests et les traces.
    """
    msg_lower = message.lower()
    if any(kw in msg_lower for kw in _PRODUCT_KEYWORDS):
        return "product_render"
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

# Sujets ArtisticIntent compatibles avec product_render — H.4.2.
# Conservateur : seuls les sujets clairement "objet produit".
# Aligné sur les labels retournés par artistic_intent._SUBJECT_RULES.
_PRODUCT_INTENT_SUBJECTS = (
    "bouteille", "flacon", "parfum",
    "produit", "product",
    "mockup", "maquette", "packaging", "packshot",
    "cube", "sphère", "sphere",
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

    Règles (conservatrices) :
      - medium == "3d_scene" ET subject_main ∈ _INTERIOR_INTENT_SUBJECTS
          → interior_space
      - medium == "product_render" ET subject_main ∈ _PRODUCT_INTENT_SUBJECTS
          → product_render   (H.4.2)
      - sinon → None
    """
    if intent is None:
        return None

    medium = _intent_field(intent, "medium")
    if medium not in ("3d_scene", "product_render"):
        return None

    subject_main = _intent_field(intent, "subject_main") or ""
    if not isinstance(subject_main, str):
        return None

    subject_lower = subject_main.lower()

    if medium == "3d_scene":
        if any(kw in subject_lower for kw in _INTERIOR_INTENT_SUBJECTS):
            return TEMPLATE_INTERIOR_SPACE
        return None

    # medium == "product_render"
    if any(kw in subject_lower for kw in _PRODUCT_INTENT_SUBJECTS):
        return TEMPLATE_PRODUCT_RENDER
    return None


def get_template_name_from_intent(intent: object) -> str | None:
    """Nom du template sélectionné via l'intent, ou None."""
    scaffold = select_template_from_intent(intent)
    if scaffold is TEMPLATE_INTERIOR_SPACE:
        return "interior_space"
    if scaffold is TEMPLATE_PRODUCT_RENDER:
        return "product_render"
    return None


# ---------------------------------------------------------------------------
# Template specs déclaratives — H.4.3-C : Scaffold fidelity / Static QA
# ---------------------------------------------------------------------------
# Spec minimale par template pour permettre une validation statique du
# scene.py final produit par le LLM (avant ou indépendamment de Blender).
#
# - required_objects : noms d'objets que le scene.py DOIT mentionner.
#   La détection est faite par recherche de la chaîne exacte du nom dans
#   le texte du script (.name = "X" ou commentaire descriptif).
# - forbidden_prefixes : préfixes de noms d'objets interdits dans ce
#   template, repérés par recherche de la chaîne du préfixe immédiatement
#   suivie d'un identifiant (ex. "Wall_Back", "Wall_Left").
#
# Ces specs n'ont AUCUN impact runtime sur la sélection ou la génération.
# Elles servent uniquement à validate_scene_py_against_template().
# ---------------------------------------------------------------------------

TEMPLATE_SPECS: dict[str, dict[str, list[str]]] = {
    "product_render": {
        "required_objects": [
            "Backdrop_Plane",
            "Pedestal",
            "Product_Subject",
            "Camera",
            "Key_Light",
        ],
        "forbidden_prefixes": ["Wall_"],
    },
    "interior_space": {
        "required_objects": [
            "Floor_Plane",
            "Wall_Back",
            "Wall_Left",
            "Wall_Right",
            "Main_Subject",
            "Camera",
            "Key_Light",
        ],
        "forbidden_prefixes": [],
    },
}


def get_template_spec(template_name: str | None) -> dict[str, list[str]] | None:
    """
    Retourne la spec déclarative associée à un template, ou None si le
    template est inconnu ou si template_name vaut None.

    Le dict retourné expose au minimum :
      - "required_objects" : list[str]
      - "forbidden_prefixes" : list[str]

    Ne lève jamais d'exception : appelable depuis n'importe quel point
    du pipeline sans contrôle préalable.
    """
    if not template_name:
        return None
    return TEMPLATE_SPECS.get(template_name)
