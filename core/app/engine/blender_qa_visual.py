"""
H.4.5 — QA visuelle V0 / Sanity check cadrage preview.png.
H.6.10 — visual_contract_v1 : segmentation sujet/fond relative au fond.

Fonctions PURES pour analyser preview.png via Pillow (pas de numpy).
Ne requiert pas Blender. Testable hors VM.

Historique segmentation :
  V0 (H.4.5)   : seuil absolu THRESHOLD_FOREGROUND (30/255), calibré pour le
    fond neutre EEVEE (~13/255). Défaillant sur fond clair / sujet sombre
    (finding B4, audit 2026-06-10) : le fond passait pour le sujet
    (subject_area_ratio=1.0 "passed" dans le même rapport que decor_dominates).
  V1 (H.6.10)  : la luminance médiane du FOND est estimée sur les zones
    périphériques (hero_framing.background_pixels, posé en H.6.9), puis
    foreground = pixels s'écartant de cette médiane de plus de
    FOREGROUND_DELTA. Fonctionne fond clair/sujet sombre ET fond
    sombre/sujet clair. Fallback : si la mesure périphérique est impossible
    (image dégénérée), retour au seuil absolu V0.

  AUCUN seuil de décision n'a changé (THRESHOLD_* identiques) : seule la
  segmentation est corrigée. Le faux positif decor_dominates sur fond
  uniforme disparaît parce que le gate foreground_area_ratio cesse d'être
  leurré, pas parce qu'un seuil a été relevé.
"""
from __future__ import annotations

import math
from pathlib import Path

from app.engine.hero_framing import background_pixels

# ---------------------------------------------------------------------------
# Violations visuelles — H.4.5
# ---------------------------------------------------------------------------

V_LOW_CONTRAST         = "low_contrast"
V_SUBJECT_TOO_SMALL    = "subject_too_small"
V_SUBJECT_OUT_OF_FRAME = "subject_out_of_frame"
V_SUBJECT_OFFCENTER    = "subject_offcenter"
V_DECOR_DOMINATES      = "decor_dominates"
V_VISUAL_QA_ERROR      = "visual_qa_error"

# ---------------------------------------------------------------------------
# Seuils V0 — constantes nommées, modifiables sans toucher à la logique
# ---------------------------------------------------------------------------

THRESHOLD_STD_LUMINANCE  = 8.0   # std < 8/255 → image quasi-uniforme
THRESHOLD_SUBJECT_AREA   = 0.05  # bbox_area/total_area < 5 % → sujet trop petit
THRESHOLD_SUBJECT_OFFSET = 0.40  # distance_centre/demi-diag > 40 % → sujet dans un coin
THRESHOLD_FOREGROUND     = 30    # V0 (fallback) : pixels > 30/255 considérés "sujet"
# THRESHOLD_FOREGROUND : seuil absolu calibré pour world.color = (0.05, 0.05, 0.05)
# ≈ 13/255. Conservé comme FALLBACK si la médiane périphérique est impossible.

# H.6.10 — segmentation relative au fond (visual_contract_v1).
# Un pixel est "sujet" si |p - médiane_fond| > FOREGROUND_DELTA. La valeur 25
# est choisie au-dessus du bruit de dégradé EEVEE du backdrop (~15 niveaux
# observés sur les smokes 2026-06-12) et en-dessous du contraste minimal
# sujet/fond attendu d'un packshot lisible.
FOREGROUND_DELTA         = 25
SEGMENTATION_METHOD_V1   = "background_relative_v1"
SEGMENTATION_METHOD_V0   = "absolute_threshold_v0_fallback"

# H.4.5.1 — détection décor dominant via histogramme + triple gate
THRESHOLD_DECOR_DOMINANCE     = 0.45  # bin dominant > 45 % des pixels → bande luminance massive
THRESHOLD_FOREGROUND_DOMINANT = 0.75  # > 75 % pixels > THRESHOLD_FOREGROUND → bbox quasi full-frame pathologique
HISTOGRAM_BIN_WIDTH           = 32    # 8 bins de 32 valeurs de luminance (256/32)


# ---------------------------------------------------------------------------
# Utilitaires internes
# ---------------------------------------------------------------------------

def _load_grayscale(render_path: str):
    """Ouvre preview.png et retourne une image PIL en mode 'L'. None si erreur."""
    try:
        from PIL import Image
        return Image.open(render_path).convert("L")
    except Exception:
        return None


def _background_median(img) -> int | None:
    """
    Médiane de luminance du fond, estimée sur les zones périphériques
    (hero_framing.background_pixels — hors sujet par construction pour un
    packshot centré-bas). None si la mesure est impossible. Pure.
    """
    try:
        pixels = sorted(background_pixels(img))
    except Exception:
        return None
    if not pixels:
        return None
    return pixels[len(pixels) // 2]


def _foreground_mask(img):
    """
    Centralise la segmentation sujet/fond (H.6.10 — visual_contract_v1).

    V1 : pixels s'écartant de la médiane du fond de plus de FOREGROUND_DELTA
    → 255 (sujet), sinon 0. Robuste fond clair ET fond sombre.
    Fallback V0 (médiane impossible) : seuil absolu THRESHOLD_FOREGROUND.
    """
    bg_median = _background_median(img)
    if bg_median is None:
        return img.point(lambda p: 255 if p > THRESHOLD_FOREGROUND else 0)
    return img.point(
        lambda p: 255 if abs(p - bg_median) > FOREGROUND_DELTA else 0
    )


def _segmentation_method(img) -> str:
    """Nom de la méthode de segmentation effectivement applicable. Pure."""
    return (
        SEGMENTATION_METHOD_V1
        if _background_median(img) is not None
        else SEGMENTATION_METHOD_V0
    )


def _empty_checks() -> dict:
    return {
        "luminance_contrast":    {"status": "skipped"},
        "subject_bbox_detected": {"status": "skipped"},
        "subject_area_ratio":    {"status": "skipped"},
        "subject_centering":     {"status": "skipped"},
        "decor_dominance":       {"status": "skipped"},
    }


# ---------------------------------------------------------------------------
# Checks individuels — fonctions PURES, testables indépendamment
# ---------------------------------------------------------------------------

def check_luminance_contrast(img) -> dict:
    """
    Vérifie que l'image n'est pas quasi-monochrome.
    Calcul mean / std en pur Python (sans numpy).
    Violation : V_LOW_CONTRAST si std < THRESHOLD_STD_LUMINANCE.
    """
    raw = img.tobytes()  # mode L : 1 octet par pixel, valeur 0-255
    n = len(raw)
    if n == 0:
        return {
            "status": "skipped",
            "violations": [],
            "mean_luminance": 0.0,
            "std_luminance": 0.0,
            "threshold_std": THRESHOLD_STD_LUMINANCE,
        }
    mean = sum(raw) / n
    variance = sum((p - mean) ** 2 for p in raw) / n
    std = math.sqrt(variance)
    status = "degraded" if std < THRESHOLD_STD_LUMINANCE else "passed"
    return {
        "status": status,
        "violations": [V_LOW_CONTRAST] if status == "degraded" else [],
        "mean_luminance": round(mean, 2),
        "std_luminance": round(std, 2),
        "threshold_std": THRESHOLD_STD_LUMINANCE,
    }


def check_subject_bbox_detected(img) -> dict:
    """
    Vérifie qu'au moins un pixel 'sujet' est visible dans le frame.
    Violation : V_SUBJECT_OUT_OF_FRAME si la bbox est None (rien au-dessus du seuil).
    """
    bbox = _foreground_mask(img).getbbox()
    if bbox is None:
        return {
            "status": "degraded",
            "violations": [V_SUBJECT_OUT_OF_FRAME],
            "bbox": None,
            "details": "Aucun pixel sujet détecté au-dessus du seuil foreground",
        }
    return {
        "status": "passed",
        "violations": [],
        "bbox": list(bbox),
    }


def check_subject_area_ratio(img) -> dict:
    """
    Vérifie que le sujet occupe au moins THRESHOLD_SUBJECT_AREA de l'image.
    Violation : V_SUBJECT_TOO_SMALL si bbox_area/total_area < seuil.
    Skipped si aucun pixel sujet (V_SUBJECT_OUT_OF_FRAME déjà dans bbox_check).
    """
    w, h = img.size
    total = w * h
    bbox = _foreground_mask(img).getbbox()
    if bbox is None:
        return {
            "status": "skipped",
            "violations": [],
            "subject_area_ratio": 0.0,
            "threshold_area": THRESHOLD_SUBJECT_AREA,
            "details": "Aucun pixel sujet — voir subject_bbox_detected",
        }
    left, top, right, bottom = bbox
    area_ratio = (right - left) * (bottom - top) / total if total > 0 else 0.0
    status = "degraded" if area_ratio < THRESHOLD_SUBJECT_AREA else "passed"
    return {
        "status": status,
        "violations": [V_SUBJECT_TOO_SMALL] if status == "degraded" else [],
        "subject_area_ratio": round(area_ratio, 4),
        "threshold_area": THRESHOLD_SUBJECT_AREA,
    }


def check_subject_centering(img) -> dict:
    """
    Vérifie que le sujet n'est pas coincé dans un coin.
    Métrique : distance bbox_centre / image_centre, normalisée par le demi-diagonal.
    Violation : V_SUBJECT_OFFCENTER si offset_ratio > THRESHOLD_SUBJECT_OFFSET.
    Skipped si aucun pixel sujet (V_SUBJECT_OUT_OF_FRAME déjà dans bbox_check).
    """
    w, h = img.size
    bbox = _foreground_mask(img).getbbox()
    if bbox is None:
        return {
            "status": "skipped",
            "violations": [],
            "offset_ratio": 1.0,
            "threshold_offset": THRESHOLD_SUBJECT_OFFSET,
            "details": "Aucun pixel sujet — voir subject_bbox_detected",
        }
    left, top, right, bottom = bbox
    cx_bbox = (left + right) / 2
    cy_bbox = (top + bottom) / 2
    cx_img  = w / 2
    cy_img  = h / 2
    half_diag = math.sqrt((w / 2) ** 2 + (h / 2) ** 2)
    dist = math.sqrt((cx_bbox - cx_img) ** 2 + (cy_bbox - cy_img) ** 2)
    offset_ratio = dist / half_diag if half_diag > 0 else 0.0
    status = "degraded" if offset_ratio > THRESHOLD_SUBJECT_OFFSET else "passed"
    return {
        "status": status,
        "violations": [V_SUBJECT_OFFCENTER] if status == "degraded" else [],
        "offset_ratio": round(offset_ratio, 4),
        "threshold_offset": THRESHOLD_SUBJECT_OFFSET,
    }


def check_decor_dominance(img) -> dict:
    """
    H.4.5.1 — Détecte le faux négatif spécifique : une bande de luminance domine
    l'histogramme ET le foreground mask couvre quasi tout le frame (pathologie :
    le décor est compté comme sujet par les checks bbox/area_ratio/centering).

    Triple gate pour éviter les faux positifs sur packshots minimalistes valides :
      1. dominant_ratio > THRESHOLD_DECOR_DOMINANCE   (bande luminance dominante)
      2. std_luminance >= THRESHOLD_STD_LUMINANCE     (pas déjà monochrome)
      3. foreground_area_ratio > THRESHOLD_FOREGROUND_DOMINANT
         (segmentation actuelle leurrée : décor passe le seuil foreground)

    Packshot minimaliste valide (fond EEVEE sombre + sujet large) :
      bin dominant élevé, std élevé, mais foreground_ratio bas (~25 %) → passed.
    Faux négatif observé (backdrop mid-gray + produit minuscule) :
      bin dominant élevé, std modéré, foreground_ratio ~95 % → degraded.
    Image monochrome : std = 0 → gate 2 ferme → passed (déjà flaggée par luminance_contrast).
    """
    raw = img.tobytes()
    n = len(raw)
    if n == 0:
        return {
            "status": "skipped",
            "violations": [],
            "dominant_ratio": 0.0,
            "threshold_dominance": THRESHOLD_DECOR_DOMINANCE,
            "foreground_area_ratio": 0.0,
            "threshold_foreground_dominant": THRESHOLD_FOREGROUND_DOMINANT,
            "bin_width": HISTOGRAM_BIN_WIDTH,
            "details": None,
        }

    # Histogramme : counts par valeur 0-255, puis regroupement en bins de largeur fixe
    counts = [0] * 256
    for p in raw:
        counts[p] += 1
    n_bins = 256 // HISTOGRAM_BIN_WIDTH
    bins = [
        sum(counts[i * HISTOGRAM_BIN_WIDTH:(i + 1) * HISTOGRAM_BIN_WIDTH])
        for i in range(n_bins)
    ]
    dominant_count = max(bins)
    dominant_ratio = dominant_count / n

    # std luminance (gate 2)
    mean = sum(raw) / n
    variance = sum((p - mean) ** 2 for p in raw) / n
    std = math.sqrt(variance)

    # foreground area ratio (gate 3) — MÊME segmentation que _foreground_mask
    # (H.6.10 : relative au fond ; le gate cesse d'être leurré par un
    # backdrop uniforme qui passait le seuil absolu V0).
    fg_raw = _foreground_mask(img).tobytes()
    foreground_count = sum(1 for p in fg_raw if p)
    foreground_ratio = foreground_count / n

    is_dominant            = dominant_ratio > THRESHOLD_DECOR_DOMINANCE
    is_not_monochrome      = std >= THRESHOLD_STD_LUMINANCE
    is_foreground_dominant = foreground_ratio > THRESHOLD_FOREGROUND_DOMINANT

    triggered = is_dominant and is_not_monochrome and is_foreground_dominant
    status = "degraded" if triggered else "passed"
    violations = [V_DECOR_DOMINATES] if triggered else []

    return {
        "status": status,
        "violations": violations,
        "dominant_ratio": round(dominant_ratio, 4),
        "threshold_dominance": THRESHOLD_DECOR_DOMINANCE,
        "foreground_area_ratio": round(foreground_ratio, 4),
        "threshold_foreground_dominant": THRESHOLD_FOREGROUND_DOMINANT,
        "bin_width": HISTOGRAM_BIN_WIDTH,
        "details": (
            "Décor uniforme remplit le frame, sujet probablement noyé "
            "(bande luminance dominante + foreground quasi full-frame)"
            if triggered else None
        ),
    }


# ---------------------------------------------------------------------------
# Orchestrateur public
# ---------------------------------------------------------------------------

def run_visual_qa(render_path: str | None) -> dict:
    """
    Lance tous les checks visuels V0 sur preview.png.

    Ne lève jamais d'exception. Retourne toujours un dict avec :
      - status       : "passed" | "degraded" | "skipped" | "unavailable"
      - violations   : violations critiques à remonter dans scene_report.violations
      - checks       : détail par check (luminance_contrast, subject_bbox_detected,
                       subject_area_ratio, subject_centering)

    "skipped"    : render_path absent, fichier introuvable, ou erreur de lecture.
    "unavailable": Pillow non installé.
    "degraded"   : au moins un check critique échoue.
    "passed"     : tous les checks passent.
    """
    if not render_path or not Path(render_path).exists():
        return {
            "status": "skipped",
            "violations": [],
            "checks": _empty_checks(),
        }

    try:
        from PIL import Image as _PIL_Image  # noqa: F401
    except ImportError:
        return {
            "status": "unavailable",
            "violations": [],
            "checks": _empty_checks(),
            "details": "Pillow non installé — pip install Pillow",
        }

    try:
        img = _load_grayscale(render_path)
        if img is None:
            return {
                "status": "skipped",
                "violations": [V_VISUAL_QA_ERROR],
                "checks": _empty_checks(),
                "details": "Impossible de lire preview.png",
            }

        lum_check    = check_luminance_contrast(img)
        bbox_check   = check_subject_bbox_detected(img)
        area_check   = check_subject_area_ratio(img)
        center_check = check_subject_centering(img)
        decor_check  = check_decor_dominance(img)  # H.4.5.1

        checks = {
            "luminance_contrast":    lum_check,
            "subject_bbox_detected": bbox_check,
            "subject_area_ratio":    area_check,
            "subject_centering":     center_check,
            "decor_dominance":       decor_check,
        }

        violations: list[str] = []
        for check in checks.values():
            violations.extend(check.get("violations", []))

        return {
            "status": "degraded" if violations else "passed",
            "violations": violations,
            "checks": checks,
            # H.6.10 — traçabilité de la méthode de segmentation utilisée.
            "segmentation": _segmentation_method(img),
        }

    except Exception as exc:
        return {
            "status": "skipped",
            "violations": [V_VISUAL_QA_ERROR],
            "checks": _empty_checks(),
            "details": f"Erreur inattendue : {exc}",
        }
