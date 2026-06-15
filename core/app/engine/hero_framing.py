"""
H.6.9 — hero_framing_v1.

Fonctions PURES de contrôle d'exposition et de cadrage product_render.
Malgré le nom de la phase, le levier principal validé (2026-06-10) est
l'EXPOSITION : énergie key, ratio key:fill, albédo backdrop. La caméra
n'est touchée qu'en contrôle minimal si l'occupation verticale projetée
du sujet sort des bornes [HERO_OCCUPANCY_MIN, HERO_OCCUPANCY_MAX].

Ce module regroupe toutes les constantes et formules de la phase pour
qu'elle reste lisible et réversible en un seul endroit :
- projection géométrique (occupation verticale du sujet dans le frame) ;
- facteur d'ajustement de distance caméra borné ;
- cap d'albédo backdrop (consommé par product_render_builder) ;
- mesure de luminance du fond par zones périphériques (indicateur de
  contrôle — PAS un nouveau contrat visuel, ne remplace pas la QA).

Pas d'I/O sauf `background_luminance_stats` (lecture preview.png via
Pillow, best-effort). Pas d'import bpy.
"""
from __future__ import annotations

import math
from pathlib import Path

# ---------------------------------------------------------------------------
# Bornes d'occupation verticale projetée (invariant de contrôle H.6.9)
# ---------------------------------------------------------------------------
# Les proportions actuelles des sujets sont validées humainement
# (2026-06-10) : l'ajustement caméra ne se déclenche que si l'occupation
# sort SIGNIFICATIVEMENT des bornes (tolérance), et ramène au plus près
# de la borne violée — jamais vers un cadrage "optimisé".

HERO_OCCUPANCY_MIN = 0.25
HERO_OCCUPANCY_MAX = 0.55
HERO_OCCUPANCY_TOLERANCE = 0.02  # déclenche seulement sous 0.23 / au-dessus 0.57

# Facteur multiplicatif appliqué à la distance caméra→sujet.
# < 1 rapproche, > 1 recule. Bornes serrées : une passe corrige au plus
# ±30/40 % de distance ; si l'invariant reste violé après clamp, c'est
# rapporté dans hero_framing.json, pas forcé.
HERO_DISTANCE_FACTOR_MIN = 0.70
HERO_DISTANCE_FACTOR_MAX = 1.40

# Distance minimale caméra→centre sujet, pour éviter zoom extrême et
# clipping (clip_start Blender par défaut : 0.1 m).
HERO_MIN_CAMERA_DISTANCE = 0.45

# Capteur effectif du rendu preview : caméra Blender par défaut
# sensor_width=36 mm, sensor_fit='AUTO', rendu carré 512x512 → le FOV
# vertical dérive des 36 mm (et non 24 mm comme le supposait le
# commentaire H.4.8.1 du corrector).
CAMERA_SENSOR_MM = 36.0

# ---------------------------------------------------------------------------
# Exposition backdrop
# ---------------------------------------------------------------------------
# Un backdrop neutral_gray (albédo 0.5) sortait déjà à ~240/255 sous la
# key 200 W : le cap limite la contribution des backdrops clairs (white
# 0.95 → 0.70) sans toucher aux couleurs sombres ni à l'alpha.

BACKDROP_ALBEDO_CAP = 0.70

# ---------------------------------------------------------------------------
# Mesure du fond par zones périphériques (indicateur de contrôle)
# ---------------------------------------------------------------------------
# Le masque foreground de blender_qa_visual est défaillant sur fond clair
# (B4) : on ne l'utilise PAS. À la place, zones géométriquement hors
# sujet pour un packshot centré-bas : bande supérieure pleine largeur +
# colonnes latérales sur la moitié haute de l'image.

BACKGROUND_TOP_BAND_RATIO = 0.12     # hauteur de la bande supérieure
BACKGROUND_SIDE_BAND_RATIO = 0.10    # largeur des colonnes latérales
BACKGROUND_SIDE_BAND_BOTTOM = 0.70   # les colonnes s'arrêtent à 70 % de la hauteur
BACKGROUND_CLIPPED_LEVEL = 250       # pixel >= 250/255 considéré "cramé"

# Cible de fond non cramé (critère de succès 03_PLAN_ACTION) :
# luminance médiane fond dans [80, 210].
BACKGROUND_MEDIAN_TARGET = (80, 210)

HERO_FRAMING_REPORT_FILENAME = "hero_framing.json"


# ---------------------------------------------------------------------------
# Politique de correction de cadrage — fonctions PURES (V1.1a)
# ---------------------------------------------------------------------------
# Unification de la métrique (Décision 17) : l'occupation est désormais
# mesurée UNE seule fois par framing_contract.occupancy_from_scene (NDC,
# projection des 8 coins). Ce module ne CALCULE plus d'occupation — il
# consomme un scalaire NDC en entrée et ne porte que la *politique* :
# bande de déclenchement, cible, clamp, arithmétique de distance et
# qualification du résultat (résidu, clamp, cible atteinte).
#
# Commit 1 (V1.1a) : politique "retour à la borne violée" — la cible est
# DYNAMIQUE (MIN si sous-cadré, MAX si sur-cadré). Le commit 2 remplacera
# `target_occupancy_for` par la cible fixe HERO_OCCUPANCY_TARGET (~0.30)
# et recalibrera le clamp : le schéma des champs rapportés reste identique.


def target_occupancy_for(occupancy: float) -> float | None:
    """
    Occupation NDC cible pour la politique courante, ou None si no-op.

    - occupation dans [MIN, MAX] (ou dégénérée) → None (no-op STRICT) ;
    - sous-cadré (< MIN) → MIN ;  - sur-cadré (> MAX) → MAX.

    Commit 2 : ce corps deviendra `return HERO_OCCUPANCY_TARGET` (hors bande).
    """
    if occupancy <= 0:
        return None
    if occupancy < HERO_OCCUPANCY_MIN:
        return HERO_OCCUPANCY_MIN
    if occupancy > HERO_OCCUPANCY_MAX:
        return HERO_OCCUPANCY_MAX
    return None


def requested_factor(occupancy: float) -> float:
    """
    Facteur de distance caméra AVANT clamp (occ / cible). 1.0 si no-op.
    occ ∝ 1/distance (1ʳᵉ approximation) : le résultat réel est RE-MESURÉ en
    NDC après déplacement, jamais supposé exact.
    """
    target = target_occupancy_for(occupancy)
    if target is None or target <= 0:
        return 1.0
    return occupancy / target


def clamp_factor(factor: float) -> float:
    """Borne le facteur dans [FACTOR_MIN, FACTOR_MAX]. 1.0 (no-op) préservé."""
    if factor == 1.0:
        return 1.0
    return max(HERO_DISTANCE_FACTOR_MIN, min(factor, HERO_DISTANCE_FACTOR_MAX))


def hero_distance_factor(occupancy: float) -> float:
    """Facteur de distance caméra FINAL (clampé) pour la politique courante."""
    return clamp_factor(requested_factor(occupancy))


def is_clamped(occupancy: float) -> bool:
    """Vrai si le clamp [FACTOR_MIN, FACTOR_MAX] a modifié le facteur demandé."""
    return requested_factor(occupancy) != hero_distance_factor(occupancy)


def clamp_distance(distance: float) -> float:
    """Distance bornée par HERO_MIN_CAMERA_DISTANCE (anti zoom extrême/clipping)."""
    return max(distance, HERO_MIN_CAMERA_DISTANCE)


def hero_adjusted_distance(distance: float, occupancy: float) -> float:
    """
    Distance caméra→sujet ajustée, bornée par HERO_MIN_CAMERA_DISTANCE.
    Identique à `distance` si l'occupation est dans la bande (no-op).
    """
    return clamp_distance(distance * hero_distance_factor(occupancy))


def occupancy_residual(occupancy_after: float, target: float) -> float:
    """Écart signé occupation obtenue − cible (après re-mesure NDC)."""
    return occupancy_after - target


def target_reached(occupancy_after: float, target: float,
                   tolerance: float = HERO_OCCUPANCY_TOLERANCE) -> bool:
    """
    Cible atteinte si |occ_après − cible| ≤ tolérance. Tolérance EXPLICITE
    (pas d'égalité flottante). Indépendant de `clamped` : une correction peut
    être clampée tout en finissant dans la tolérance, ou inversement.
    """
    return abs(occupancy_after - target) <= tolerance


def correction_outcome(occupancy_before: float,
                       occupancy_after: float | None) -> dict:
    """
    Champs de qualification **liés à la cible** du correcteur (purs, sans bpy
    ni framing_contract). Source unique de cette sémantique pour le runtime
    et les tests.

    - **no-op** (occupation dans la bande → pas de cible corrective) : les
      trois champs restent `None`. `target_reached=None`, PAS `True` : aucune
      cible n'existe, donc rien à « atteindre ».
    - **corrigé** : `occupancy_residual` (signé) et `target_reached` (tolérance
      du correcteur) sont renseignés.

    NB : la conformité STRICTE au contrat (`in_contract_band_after`) est volon-
    tairement HORS de ce dict — elle relève de framing_contract.in_occupancy_band,
    jamais de la tolérance du correcteur (séparation des autorités, V1.1b).
    """
    target = target_occupancy_for(occupancy_before)
    out = {
        "target_occupancy": target,
        "occupancy_residual": None,
        "target_reached": None,
    }
    if occupancy_after is not None and target is not None:
        out["occupancy_residual"] = occupancy_after - target
        out["target_reached"] = target_reached(occupancy_after, target)
    return out


# ---------------------------------------------------------------------------
# Cap albédo backdrop
# ---------------------------------------------------------------------------

def cap_backdrop_albedo(
    rgba: tuple[float, float, float, float],
    cap: float = BACKDROP_ALBEDO_CAP,
) -> tuple[float, float, float, float]:
    """Clamp chaque canal RGB à `cap`. Alpha préservé. Pure."""
    r, g, b, a = rgba
    return (min(r, cap), min(g, cap), min(b, cap), a)


# ---------------------------------------------------------------------------
# Luminance du fond — zones périphériques
# ---------------------------------------------------------------------------

def background_pixels(img) -> list[int]:
    """
    Extrait les pixels des zones périphériques d'une image PIL mode 'L' :
    bande supérieure pleine largeur + colonnes latérales gauche/droite
    jusqu'à BACKGROUND_SIDE_BAND_BOTTOM de la hauteur. Pour un packshot
    centré-bas, ces zones sont hors sujet par construction géométrique.
    """
    w, h = img.size
    top_h = max(1, int(h * BACKGROUND_TOP_BAND_RATIO))
    side_w = max(1, int(w * BACKGROUND_SIDE_BAND_RATIO))
    side_bottom = max(top_h, int(h * BACKGROUND_SIDE_BAND_BOTTOM))

    pixels: list[int] = []
    pixels += list(img.crop((0, 0, w, top_h)).tobytes())
    pixels += list(img.crop((0, top_h, side_w, side_bottom)).tobytes())
    pixels += list(img.crop((w - side_w, top_h, w, side_bottom)).tobytes())
    return pixels


def background_columns(img) -> list[list[int]]:
    """
    Variante PAR COLONNE de background_pixels (bbox_gradient_v1) : retourne, pour
    chaque colonne x, la liste des pixels de fond périphériques de cette colonne
    — bande supérieure pour tout x, plus la portion de colonne latérale pour les
    bords gauche/droit. Même géométrie de bandes que background_pixels (source
    unique), pour estimer une référence de fond *locale* robuste aux dégradés
    latéraux du backdrop. Pure.
    """
    w, h = img.size
    top_h = max(1, int(h * BACKGROUND_TOP_BAND_RATIO))
    side_w = max(1, int(w * BACKGROUND_SIDE_BAND_RATIO))
    side_bottom = max(top_h, int(h * BACKGROUND_SIDE_BAND_BOTTOM))

    # Bande supérieure (row-major) : la colonne x est le slice top[x::w].
    top = img.crop((0, 0, w, top_h)).tobytes()
    cols: list[list[int]] = [list(top[x::w]) for x in range(w)]

    # Colonnes latérales : étendent verticalement les seules colonnes de bord.
    if side_bottom > top_h:
        left = img.crop((0, top_h, side_w, side_bottom)).tobytes()
        right = img.crop((w - side_w, top_h, w, side_bottom)).tobytes()
        for xi in range(side_w):
            cols[xi].extend(left[xi::side_w])
            cols[w - side_w + xi].extend(right[xi::side_w])
    return cols


def background_luminance_stats(render_path: str | None) -> dict:
    """
    Statistiques de luminance du fond sur preview.png, par zones
    périphériques (méthode : voir `background_pixels`).

    Indicateur de contrôle H.6.9 — ne remplace pas la QA visuelle et
    n'émet aucune violation. Médiane + p90 + max + ratio de pixels
    cramés (>= BACKGROUND_CLIPPED_LEVEL), pour qu'un fond partiellement
    cramé ne passe pas juste parce que la médiane est correcte.

    Best-effort : status "skipped" si fichier absent/illisible,
    "unavailable" si Pillow manque. Ne lève jamais.
    """
    base = {
        "method": "peripheral_bands_v1",
        "median": None,
        "p90": None,
        "max": None,
        "clipped_ratio": None,
        "pixels_sampled": 0,
        "median_target": list(BACKGROUND_MEDIAN_TARGET),
    }
    if not render_path or not Path(render_path).exists():
        return {"status": "skipped", **base}
    try:
        from PIL import Image
    except ImportError:
        return {"status": "unavailable", **base}
    try:
        img = Image.open(render_path).convert("L")
        pixels = sorted(background_pixels(img))
        n = len(pixels)
        if n == 0:
            return {"status": "skipped", **base}
        clipped = sum(1 for p in pixels if p >= BACKGROUND_CLIPPED_LEVEL)
        return {
            "status": "ok",
            "method": "peripheral_bands_v1",
            "median": pixels[n // 2],
            "p90": pixels[min(n - 1, (n * 9) // 10)],
            "max": pixels[-1],
            "clipped_ratio": round(clipped / n, 4),
            "pixels_sampled": n,
            "median_target": list(BACKGROUND_MEDIAN_TARGET),
        }
    except Exception:
        return {"status": "skipped", **base}
