"""
framing_contract — Contrat de cadrage par projection géométrique (§9.2, V1).

Décision 17 : l'autorité sur le **cadrage spatial** revient à ce contrat
géométrique projeté (a priori, depuis la scène 3D + la caméra), pas à la QA
pixel (qui reste un signal perceptuel de diagnostic).

Module **pur** : aucun `bpy`, aucune I/O. Il reçoit des données brutes
(matrice de vue caméra + paramètres optiques + 8 coins MONDE du sujet) et
calcule la bbox écran et les invariants de composition.

Convention de projection (figée V1) : **NDC Blender**, origine **bas-gauche**
`(0,0)`, valeurs **non clampées** (un point hors cadre donne u/v hors [0,1]),
profondeur `z > 0` devant la caméra. `world_to_camera_view` est l'oracle de
validation croisée (tests, si Blender présent).

Périmètre V1 (borné) : invariants **occupation**, **centrage**, **in-frame**.
Aucun invariant de contact, visée, focale ou exposition.
"""
from __future__ import annotations

import math

# ---------------------------------------------------------------------------
# Violations de cadrage (préfixe framing_ pour les distinguer du pixel)
# ---------------------------------------------------------------------------

V_FRAMING_OCCUPANCY_OUT = "framing_occupancy_out"
V_FRAMING_OFFCENTER     = "framing_offcenter"
V_FRAMING_OUT_OF_FRAME  = "framing_out_of_frame"
V_FRAMING_BEHIND_CAMERA = "framing_behind_camera"

# ---------------------------------------------------------------------------
# Seuils V1 (§9.2) — constantes nommées
# ---------------------------------------------------------------------------

OCCUPANCY_MIN = 0.25   # hauteur projetée du sujet / hauteur frame
OCCUPANCY_MAX = 0.55
CENTER_U_MIN  = 0.35   # centroïde horizontal du sujet
CENTER_U_MAX  = 0.65
FRAME_MARGIN  = 0.05   # marge de sécurité : coins dans [0.05, 0.95]

METHOD_V1 = "projected_ndc_v1"

_EPS = 1e-9


# ---------------------------------------------------------------------------
# Algèbre 4x4 minimale (row-major, tuples) — pure
# ---------------------------------------------------------------------------

def _mat4_mul_point(m, p) -> tuple[float, float, float]:
    """Applique une matrice 4x4 (4 lignes de 4) à un point 3D (w=1)."""
    x, y, z = p[0], p[1], p[2]
    cx = m[0][0] * x + m[0][1] * y + m[0][2] * z + m[0][3]
    cy = m[1][0] * x + m[1][1] * y + m[1][2] * z + m[1][3]
    cz = m[2][0] * x + m[2][1] * y + m[2][2] * z + m[2][3]
    return (cx, cy, cz)


def _rot_xyz(rx: float, ry: float, rz: float):
    """Matrice de rotation 3x3 pour l'ordre d'Euler Blender 'XYZ' (R = Rz·Ry·Rx).
    Validé contre Blender par l'oracle world_to_camera_view (test)."""
    cx, sx = math.cos(rx), math.sin(rx)
    cy, sy = math.cos(ry), math.sin(ry)
    cz, sz = math.cos(rz), math.sin(rz)
    # Rz · Ry · Rx
    return (
        (cz * cy, cz * sy * sx - sz * cx, cz * sy * cx + sz * sx),
        (sz * cy, sz * sy * sx + cz * cx, sz * sy * cx - cz * sx),
        (-sy,     cy * sx,                cy * cx),
    )


def view_matrix_from_pose(location, rotation_euler) -> tuple:
    """
    Matrice de VUE (inverse de la matrice monde de la caméra) à partir d'une
    pose rigide (location + euler XYZ). Pour un transform rigide M=[R|t],
    l'inverse est [Rᵀ | −Rᵀt]. Utilisé par les fixtures de non-régression
    (constantes canoniques) ; au runtime, Blender fournit directement la
    matrice de vue. Pure.
    """
    r = _rot_xyz(rotation_euler[0], rotation_euler[1], rotation_euler[2])
    tx, ty, tz = location
    # Rᵀ
    rt = (
        (r[0][0], r[1][0], r[2][0]),
        (r[0][1], r[1][1], r[2][1]),
        (r[0][2], r[1][2], r[2][2]),
    )
    # −Rᵀ t
    nx = -(rt[0][0] * tx + rt[0][1] * ty + rt[0][2] * tz)
    ny = -(rt[1][0] * tx + rt[1][1] * ty + rt[1][2] * tz)
    nz = -(rt[2][0] * tx + rt[2][1] * ty + rt[2][2] * tz)
    return (
        (rt[0][0], rt[0][1], rt[0][2], nx),
        (rt[1][0], rt[1][1], rt[1][2], ny),
        (rt[2][0], rt[2][1], rt[2][2], nz),
        (0.0, 0.0, 0.0, 1.0),
    )


# ---------------------------------------------------------------------------
# Paramètres de projection (demi-extents du frame à profondeur 1)
# ---------------------------------------------------------------------------

def half_extents_at_unit_depth(
    lens_mm: float,
    sensor_width_mm: float = 36.0,
    sensor_height_mm: float = 24.0,
    sensor_fit: str = "AUTO",
    res_x: int = 512,
    res_y: int = 512,
    pixel_aspect_x: float = 1.0,
    pixel_aspect_y: float = 1.0,
) -> tuple[float, float]:
    """
    Demi-largeur / demi-hauteur du frame caméra à profondeur 1 (perspective).
    Reproduit la logique de capteur Blender (AUTO/HORIZONTAL/VERTICAL + aspect).
    V1 : pas de lens shift (shift_x = shift_y = 0, cf. CANONICAL_CAMERA). Pure.
    """
    aspect_x = res_x * pixel_aspect_x
    aspect_y = res_y * pixel_aspect_y
    if sensor_fit == "AUTO":
        fit = "HORIZONTAL" if aspect_x >= aspect_y else "VERTICAL"
    else:
        fit = sensor_fit
    if fit == "HORIZONTAL":
        half_w = (sensor_width_mm / 2.0) / lens_mm
        half_h = half_w * (aspect_y / aspect_x) if aspect_x else half_w
    else:
        half_h = (sensor_height_mm / 2.0) / lens_mm
        half_w = half_h * (aspect_x / aspect_y) if aspect_y else half_h
    return (half_w, half_h)


def project_point(view_matrix, half_w: float, half_h: float, p_world) -> tuple[float, float, float]:
    """
    Projette un point MONDE en NDC Blender (origine bas-gauche, non clampé).
    Retourne (u, v, z) : u,v ∈ ℝ (∈ [0,1] si dans le cadre), z = profondeur
    devant la caméra (> 0 devant). Pure.
    """
    cx, cy, cz = _mat4_mul_point(view_matrix, p_world)
    z = -cz  # caméra regarde le long de −Z ; profondeur positive devant
    if abs(z) < _EPS:
        return (0.5, 0.5, z)
    u = 0.5 + 0.5 * (cx / (half_w * z))
    v = 0.5 + 0.5 * (cy / (half_h * z))
    return (u, v, z)


def screen_bbox(view_matrix, half_w: float, half_h: float, corners_world):
    """
    bbox écran (NDC, non clampée) des coins fournis : (u0, v0, u1, v1, z_min,
    n_behind). z_min = profondeur minimale ; n_behind = nombre de coins
    derrière la caméra (z ≤ 0). Pure.
    """
    us, vs, zs, n_behind = [], [], [], 0
    for c in corners_world:
        u, v, z = project_point(view_matrix, half_w, half_h, c)
        us.append(u); vs.append(v); zs.append(z)
        if z <= 0:
            n_behind += 1
    return (min(us), min(vs), max(us), max(vs), min(zs), n_behind)


def occupancy_from_scene(view_matrix, proj: dict, corners_world) -> float:
    """
    Occupation NDC verticale du sujet (v1 − v0 de la bbox écran projetée).

    **Métrique unique** (V1.1a, Décision 17) : c'est exactement l'`occupancy`
    de `evaluate_framing` — toutes deux dérivent de `screen_bbox` (source de
    géométrie unique). Exposée comme scalaire autoportant pour être consommée
    à l'identique par la mesure, le contrôle `hero_framing` et (V1.1b)
    l'arbitrage de statut, sans réimplémenter la projection. Pure ; 0.0 si vide.
    `proj` = dict avec clés 'half_w'/'half_h'.
    """
    if not corners_world:
        return 0.0
    _, v0, _, v1, _, _ = screen_bbox(
        view_matrix, proj["half_w"], proj["half_h"], corners_world
    )
    return v1 - v0


def in_occupancy_band(occupancy: float) -> bool:
    """
    Conformité **stricte** de l'occupation au contrat [OCCUPANCY_MIN,
    OCCUPANCY_MAX]. Référence décisionnelle (V1.1b) : la tolérance de
    convergence du correcteur `hero_framing` ne l'assouplit JAMAIS — un sujet
    ramené à 0.235 par un correcteur clampé n'est pas dans le contrat, même si
    le correcteur considère sa cible « atteinte » à sa propre tolérance. Pure.
    """
    return OCCUPANCY_MIN <= occupancy <= OCCUPANCY_MAX


# ---------------------------------------------------------------------------
# Évaluation des invariants V1
# ---------------------------------------------------------------------------

def evaluate_framing(view_matrix, proj: dict, subject_corners_world) -> dict:
    """
    Évalue les invariants de cadrage V1 (occupation, centrage, in-frame) sur
    les 8 coins MONDE de la bbox 3D du sujet. `proj` = sortie de
    half_extents_at_unit_depth (clé 'half_w'/'half_h') ou dict de paramètres
    optiques. Retourne un bloc autoportant (status + violations + métriques).
    Pure ; ne lève jamais.
    """
    half_w = proj["half_w"]
    half_h = proj["half_h"]
    if not subject_corners_world:
        return {"status": "skipped", "violations": [], "method": METHOD_V1,
                "details": "Aucun coin sujet"}

    u0, v0, u1, v1, z_min, n_behind = screen_bbox(
        view_matrix, half_w, half_h, subject_corners_world
    )
    occupancy = v1 - v0
    center_u  = (u0 + u1) / 2.0
    base_v    = v0  # bas du sujet (origine bas-gauche)

    behind = n_behind > 0
    lo, hi = FRAME_MARGIN, 1.0 - FRAME_MARGIN
    in_frame = (not behind) and (lo <= u0) and (u1 <= hi) and (lo <= v0) and (v1 <= hi)

    violations: list[str] = []
    if behind:
        violations.append(V_FRAMING_BEHIND_CAMERA)
    if not (OCCUPANCY_MIN <= occupancy <= OCCUPANCY_MAX):
        violations.append(V_FRAMING_OCCUPANCY_OUT)
    if not (CENTER_U_MIN <= center_u <= CENTER_U_MAX):
        violations.append(V_FRAMING_OFFCENTER)
    if not in_frame and not behind:
        violations.append(V_FRAMING_OUT_OF_FRAME)

    return {
        "status": "passed" if not violations else "degraded",
        "violations": violations,
        "method": METHOD_V1,
        "screen_bbox": [round(u0, 4), round(v0, 4), round(u1, 4), round(v1, 4)],
        "occupancy": round(occupancy, 4),
        "center_u": round(center_u, 4),
        "base_v": round(base_v, 4),
        "in_frame": in_frame,
        "depth_min": round(z_min, 4),
        "thresholds": {
            "occupancy": [OCCUPANCY_MIN, OCCUPANCY_MAX],
            "center_u": [CENTER_U_MIN, CENTER_U_MAX],
            "frame_margin": FRAME_MARGIN,
        },
    }


# ---------------------------------------------------------------------------
# Réconciliation projeté ↔ perçu (framing_divergence, SIGNAL-ONLY)
# ---------------------------------------------------------------------------

DIVERGENCE_IOU_MIN = 0.35  # IoU sous ce seuil → projeté et perçu divergent


def screen_bbox_to_top_left_fraction(screen_bbox_uvuv):
    """
    Convertit une bbox écran NDC (origine bas-gauche) en bbox FRACTION origine
    haut-gauche (comme PIL, fractions de l'image) : [left, top, right, bottom].
    Indépendant de la résolution. Pure.
    """
    u0, v0, u1, v1 = screen_bbox_uvuv
    return [u0, 1.0 - v1, u1, 1.0 - v0]   # v haut (grand) → y top (petit)


def _iou(a, b) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    iw, ih = max(0.0, ix1 - ix0), max(0.0, iy1 - iy0)
    inter = iw * ih
    area_a = max(0.0, ax1 - ax0) * max(0.0, ay1 - ay0)
    area_b = max(0.0, bx1 - bx0) * max(0.0, by1 - by0)
    union = area_a + area_b - inter
    return inter / union if union > _EPS else 0.0


def framing_divergence(screen_bbox_uvuv, perceptual_bbox_fraction) -> dict:
    """
    Compare la bbox projetée (géométrique) à la bbox perceptuelle (visual_qa)
    via IoU, **en fractions [0,1]** (indépendant de la résolution). SIGNAL-ONLY :
    informatif, ne pilote jamais de statut bloquant. Pure.
    `perceptual_bbox_fraction` = [left, top, right, bottom] en fractions, ou None.
    """
    if not perceptual_bbox_fraction:
        return {"status": "skipped", "iou": None, "diverged": False,
                "threshold_iou": DIVERGENCE_IOU_MIN}
    projected = screen_bbox_to_top_left_fraction(screen_bbox_uvuv)
    iou = _iou(projected, perceptual_bbox_fraction)
    return {
        "status": "computed",
        "iou": round(iou, 4),
        "diverged": iou < DIVERGENCE_IOU_MIN,
        "threshold_iou": DIVERGENCE_IOU_MIN,
        "projected_bbox_fraction": [round(v, 4) for v in projected],
        "perceptual_bbox_fraction": [round(v, 4) for v in perceptual_bbox_fraction],
    }
