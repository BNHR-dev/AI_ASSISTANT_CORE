"""
H.4.5 — QA visuelle V0 / Sanity check cadrage preview.png.

Fonctions PURES pour analyser preview.png via Pillow (pas de numpy).
Ne requiert pas Blender. Testable hors VM.

Limite V0 :
  La détection sujet/fond repose sur un seuil absolu (THRESHOLD_FOREGROUND).
  Ce seuil est calibré pour le fond neutre EEVEE (~13/255, world.color = 0.05).
  Il ne fonctionne pas correctement si le fond est clair ou si le sujet est sombre.
  Isolé dans _foreground_mask() pour faciliter le remplacement futur.
"""
from __future__ import annotations

import math
from pathlib import Path

# ---------------------------------------------------------------------------
# Violations visuelles — H.4.5
# ---------------------------------------------------------------------------

V_LOW_CONTRAST         = "low_contrast"
V_SUBJECT_TOO_SMALL    = "subject_too_small"
V_SUBJECT_OUT_OF_FRAME = "subject_out_of_frame"
V_SUBJECT_OFFCENTER    = "subject_offcenter"
V_VISUAL_QA_ERROR      = "visual_qa_error"

# ---------------------------------------------------------------------------
# Seuils V0 — constantes nommées, modifiables sans toucher à la logique
# ---------------------------------------------------------------------------

THRESHOLD_STD_LUMINANCE  = 8.0   # std < 8/255 → image quasi-uniforme
THRESHOLD_SUBJECT_AREA   = 0.05  # bbox_area/total_area < 5 % → sujet trop petit
THRESHOLD_SUBJECT_OFFSET = 0.40  # distance_centre/demi-diag > 40 % → sujet dans un coin
THRESHOLD_FOREGROUND     = 30    # pixels > 30/255 considérés "sujet" vs fond EEVEE (~13/255)
# THRESHOLD_FOREGROUND : seuil absolu calibré pour world.color = (0.05, 0.05, 0.05)
# ≈ 13/255. Limite : incorrect si fond clair ou sujet très sombre.


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


def _foreground_mask(img):
    """
    Centralise la segmentation sujet/fond.
    Retourne une image binaire L : pixels > THRESHOLD_FOREGROUND → 255, sinon 0.
    Limite : seuil fixe calibré EEVEE. Voir module docstring.
    """
    return img.point(lambda p: 255 if p > THRESHOLD_FOREGROUND else 0)


def _empty_checks() -> dict:
    return {
        "luminance_contrast":    {"status": "skipped"},
        "subject_bbox_detected": {"status": "skipped"},
        "subject_area_ratio":    {"status": "skipped"},
        "subject_centering":     {"status": "skipped"},
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

        checks = {
            "luminance_contrast":    lum_check,
            "subject_bbox_detected": bbox_check,
            "subject_area_ratio":    area_check,
            "subject_centering":     center_check,
        }

        violations: list[str] = []
        for check in checks.values():
            violations.extend(check.get("violations", []))

        return {
            "status": "degraded" if violations else "passed",
            "violations": violations,
            "checks": checks,
        }

    except Exception as exc:
        return {
            "status": "skipped",
            "violations": [V_VISUAL_QA_ERROR],
            "checks": _empty_checks(),
            "details": f"Erreur inattendue : {exc}",
        }
