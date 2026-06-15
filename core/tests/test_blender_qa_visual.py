"""
Tests — H.4.5 : QA visuelle V0 / blender_qa_visual.

Fixtures PNG créées programmatiquement avec Pillow. Aucun Blender requis.

Scénarios couverts :
  - image uniforme (quasi-monochrome) → degraded via luminance_contrast
  - sujet petit en bas-gauche → degraded via subject_area_ratio + subject_centering
  - aucun pixel sujet détectable → degraded via subject_bbox_detected
  - sujet centré suffisamment grand → passed
  - render_path=None → status skipped, aucune dégradation
  - preview absente (path non existant) → status skipped, aucune dégradation
  - inspect_blend_scene avec render_path → scene_report contient visual_qa
  - inspect_blend_scene avec mauvaise preview → status global degraded
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

try:
    from PIL import Image
    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False

from app.engine.blender_qa_visual import (
    FOREGROUND_DELTA,
    SEGMENTATION_METHOD_V2,
    THRESHOLD_DECOR_DOMINANCE,
    THRESHOLD_FOREGROUND,
    THRESHOLD_FOREGROUND_DOMINANT,
    THRESHOLD_STD_LUMINANCE,
    THRESHOLD_SUBJECT_AREA,
    THRESHOLD_SUBJECT_OFFSET,
    V_DECOR_DOMINATES,
    V_LOW_CONTRAST,
    V_SUBJECT_OUT_OF_FRAME,
    V_SUBJECT_TOO_SMALL,
    V_SUBJECT_OFFCENTER,
    _background_column_reference,
    _background_median,
    _foreground_mask,
    check_decor_dominance,
    check_luminance_contrast,
    check_subject_bbox_detected,
    check_subject_area_ratio,
    check_subject_centering,
    run_visual_qa,
)
from app.engine.blender_validator import inspect_blend_scene
from app.engine import framing_contract


pytestmark = pytest.mark.skipif(
    not _PIL_AVAILABLE,
    reason="Pillow requis pour les tests QA visuelle",
)


# ---------------------------------------------------------------------------
# Helpers fixtures PIL
# ---------------------------------------------------------------------------

def _make_uniform_image(value: int = 20, size: tuple = (64, 64)) -> "Image.Image":
    """Image entièrement uniforme — simuler fond monochrome / image noire."""
    return Image.new("L", size, value)


def _make_small_subject_image(size: tuple = (128, 128)) -> "Image.Image":
    """
    Petit sujet (20×20 px) dans le coin bas-gauche d'un fond sombre.
    area_ratio ≈ 400/16384 ≈ 0.024 < THRESHOLD_SUBJECT_AREA (0.05) → sujet trop petit.
    bbox centre très excentré → subject_offcenter probable.
    """
    img = Image.new("L", size, 10)   # fond EEVEE sombre
    # Dessiner un petit rectangle bas-gauche lumineux
    w, h = size
    subject_val = 200
    for x in range(5, 25):
        for y in range(h - 25, h - 5):
            img.putpixel((x, y), subject_val)
    return img


def _make_good_image(size: tuple = (128, 128)) -> "Image.Image":
    """
    Sujet large (80×80 px) centré, fond sombre.
    area_ratio ≈ 6400/16384 ≈ 0.39 >> 0.05 → passed.
    bbox centré sur l'image → centering passed.
    std élevée → luminance_contrast passed.
    """
    img = Image.new("L", size, 10)
    w, h = size
    cx, cy = w // 2, h // 2
    r = 40  # demi-côté
    subject_val = 200
    for x in range(cx - r, cx + r):
        for y in range(cy - r, cy + r):
            img.putpixel((x, y), subject_val)
    return img


def _make_no_subject_image(size: tuple = (64, 64)) -> "Image.Image":
    """
    Image entièrement à THRESHOLD_FOREGROUND ou en-dessous → aucun pixel sujet.
    Simule sujet totalement absent ou hors cadre.
    """
    return Image.new("L", size, THRESHOLD_FOREGROUND)  # exactement au seuil → non détecté (> strict)


def _make_dominant_backdrop_image(size: tuple = (128, 128)) -> "Image.Image":
    """
    H.4.5.1 — Mime le faux négatif observé : large backdrop mid-gray avec
    variation d'éclairage (zones lit/shadow) + minuscule sujet brillant au centre.
    La variation entre zones produit un std > THRESHOLD_STD_LUMINANCE, simulant
    le rendu EEVEE réel (la preview problématique a std=20.47).

    Triple gate decor_dominance attendu :
      - dominant_ratio ~0.70 (zone "lit" majoritaire dans bin [64-95])
      - std ~10 (variation lit/shadow)
      - foreground_area_ratio ≈ 1.0 (tous pixels > THRESHOLD_FOREGROUND=30)
    → degraded via V_DECOR_DOMINATES.
    """
    img = Image.new("L", size, 90)    # zone 1 backdrop "lit", bin [64-95]
    w, h = size
    # Zone 2 "shadow" (bas du frame) — apporte de la variation pour pousser std > 8
    top_zone_height = int(h * 0.7)
    for x in range(w):
        for y in range(top_zone_height, h):
            img.putpixel((x, y), 110)  # zone 2, bin [96-127]
    # Sujet minuscule 4×4 brillant au centre
    cx, cy = w // 2, h // 2
    for x in range(cx - 2, cx + 2):
        for y in range(cy - 2, cy + 2):
            img.putpixel((x, y), 220)
    return img


def _make_blob_dominant_image(size: tuple = (128, 128)) -> "Image.Image":
    """
    H.6.10 — Pathologie decor_dominates encore valide sous la segmentation
    V1 : la périphérie est sombre (médiane fond ~15) mais une nappe claire
    (éclairage/décor) remplit ~85 % du frame. La segmentation relative
    compte la nappe comme foreground → gate 3 ouvert ; bin dominant élevé ;
    std élevé → V_DECOR_DOMINATES légitime (le « sujet » détecté est en
    réalité une nappe de décor full-frame).
    """
    img = Image.new("L", size, 200)   # nappe claire dominante
    w, h = size
    # Périphérie sombre : exactement les zones échantillonnées par
    # hero_framing.background_pixels (bande supérieure + colonnes latérales
    # jusqu'à 70 % de la hauteur), pour rester < 25 % du frame et laisser
    # la nappe claire au-dessus du gate foreground (0.75).
    top_h = max(1, int(h * 0.12))
    side_w = max(1, int(w * 0.10))
    side_bottom = int(h * 0.70)
    for x in range(w):
        for y in range(top_h):
            img.putpixel((x, y), 15)
    for y in range(top_h, side_bottom):
        for x in range(side_w):
            img.putpixel((x, y), 15)
        for x in range(w - side_w, w):
            img.putpixel((x, y), 15)
    return img


def _make_minimalist_packshot_image(size: tuple = (128, 128)) -> "Image.Image":
    """
    H.4.5.1 — Packshot minimaliste valide : fond sombre uniforme EEVEE +
    sujet large centré.
    Triple gate decor_dominance :
      - dominant_ratio élevé (fond sombre uniforme)   → gate 1 ✓
      - std élevé (sujet vs fond)                     → gate 2 ✓
      - foreground_area_ratio ~22 % (seul le sujet)   → gate 3 ✗ ferme
    → reste passed, pas de faux positif.
    """
    img = Image.new("L", size, 10)   # fond sombre uniforme, en-dessous threshold 30
    w, h = size
    cx, cy = w // 2, h // 2
    r = 30                            # sujet 60×60 ≈ 22 % du frame
    for x in range(cx - r, cx + r):
        for y in range(cy - r, cy + r):
            img.putpixel((x, y), 180)
    return img


def _make_gradient_backdrop_image(size: tuple = (128, 128)) -> "Image.Image":
    """
    bbox_gradient_v1 — backdrop en dégradé LATÉRAL reproduisant le résidu
    H.6.10 (smokes 80358b99 / c4076a23) : plateau central clair (~135) qui
    retombe à ~65 sur de fines bandes de bord (vignette d'éclairage), + sujet
    net centré. La médiane de fond GLOBALE est tirée par le centre clair (~135),
    si bien que le bord sombre s'en écarte de ≫ FOREGROUND_DELTA et est compté
    comme sujet (bbox collée au bord). La référence PAR COLONNE donne à chaque
    bord sa propre base sombre → bbox recentrée.
    """
    w, h = size
    img = Image.new("L", size)
    px = img.load()
    edge = max(1, int(w * 0.15))      # largeur des bandes de bord en dégradé
    plateau, dark = 135, 65
    for x in range(w):
        if x < edge:
            val = dark + (plateau - dark) * x // edge
        elif x > w - 1 - edge:
            val = dark + (plateau - dark) * (w - 1 - x) // edge
        else:
            val = plateau
        for y in range(h):
            px[x, y] = int(val)
    # Sujet net centré, nettement détaché du fond local (≈ tiers central).
    r = w // 6
    cx, cy = w // 2, h // 2
    for x in range(cx - r, cx + r):
        for y in range(cy - r, cy + r):
            px[x, y] = 230
    return img


def _save_png(img: "Image.Image", path: Path) -> None:
    img.save(str(path), format="PNG")


# ---------------------------------------------------------------------------
# Tests check_luminance_contrast
# ---------------------------------------------------------------------------

class TestCheckLuminanceContrast:

    def test_uniform_image_is_degraded(self):
        img = _make_uniform_image(20)
        result = check_luminance_contrast(img)
        assert result["status"] == "degraded"
        assert V_LOW_CONTRAST in result["violations"]
        assert result["std_luminance"] < THRESHOLD_STD_LUMINANCE

    def test_uniform_bright_image_is_degraded(self):
        img = _make_uniform_image(240)
        result = check_luminance_contrast(img)
        assert result["status"] == "degraded"
        assert V_LOW_CONTRAST in result["violations"]

    def test_good_image_passes(self):
        img = _make_good_image()
        result = check_luminance_contrast(img)
        assert result["status"] == "passed"
        assert result["violations"] == []
        assert result["std_luminance"] >= THRESHOLD_STD_LUMINANCE

    def test_result_has_required_keys(self):
        img = _make_uniform_image()
        result = check_luminance_contrast(img)
        for key in ("status", "violations", "mean_luminance", "std_luminance", "threshold_std"):
            assert key in result

    def test_threshold_exposed_in_result(self):
        img = _make_uniform_image()
        result = check_luminance_contrast(img)
        assert result["threshold_std"] == THRESHOLD_STD_LUMINANCE


# ---------------------------------------------------------------------------
# Tests check_subject_bbox_detected
# ---------------------------------------------------------------------------

class TestCheckSubjectBboxDetected:

    def test_no_subject_pixels_is_degraded(self):
        img = _make_no_subject_image()
        result = check_subject_bbox_detected(img)
        assert result["status"] == "degraded"
        assert V_SUBJECT_OUT_OF_FRAME in result["violations"]
        assert result["bbox"] is None

    def test_subject_present_passes(self):
        img = _make_good_image()
        result = check_subject_bbox_detected(img)
        assert result["status"] == "passed"
        assert result["violations"] == []
        assert result["bbox"] is not None
        assert len(result["bbox"]) == 4

    def test_small_subject_still_detected(self):
        img = _make_small_subject_image()
        result = check_subject_bbox_detected(img)
        # Le sujet est petit mais détectable
        assert result["status"] == "passed"
        assert result["bbox"] is not None

    def test_uniform_dark_below_threshold_is_degraded(self):
        img = _make_uniform_image(value=THRESHOLD_FOREGROUND - 1)
        result = check_subject_bbox_detected(img)
        assert result["status"] == "degraded"

    def test_uniform_image_has_no_subject(self):
        """H.6.10 — segmentation relative au fond : une image entièrement
        uniforme n'a PAS de sujet (V0 absolu disait l'inverse dès que la
        valeur dépassait le seuil : c'était le cœur du finding B4)."""
        img = _make_uniform_image(value=THRESHOLD_FOREGROUND + 1)
        result = check_subject_bbox_detected(img)
        assert result["status"] == "degraded"
        assert V_SUBJECT_OUT_OF_FRAME in result["violations"]


# ---------------------------------------------------------------------------
# Tests check_subject_area_ratio
# ---------------------------------------------------------------------------

class TestCheckSubjectAreaRatio:

    def test_small_subject_is_degraded(self):
        img = _make_small_subject_image()
        result = check_subject_area_ratio(img)
        assert result["status"] == "degraded"
        assert V_SUBJECT_TOO_SMALL in result["violations"]
        assert result["subject_area_ratio"] < THRESHOLD_SUBJECT_AREA

    def test_large_subject_passes(self):
        img = _make_good_image()
        result = check_subject_area_ratio(img)
        assert result["status"] == "passed"
        assert result["violations"] == []
        assert result["subject_area_ratio"] >= THRESHOLD_SUBJECT_AREA

    def test_no_subject_is_skipped(self):
        img = _make_no_subject_image()
        result = check_subject_area_ratio(img)
        assert result["status"] == "skipped"
        assert result["violations"] == []

    def test_result_has_required_keys(self):
        img = _make_good_image()
        result = check_subject_area_ratio(img)
        for key in ("status", "violations", "subject_area_ratio", "threshold_area"):
            assert key in result

    def test_threshold_exposed_in_result(self):
        img = _make_small_subject_image()
        result = check_subject_area_ratio(img)
        assert result["threshold_area"] == THRESHOLD_SUBJECT_AREA


# ---------------------------------------------------------------------------
# Tests check_subject_centering
# ---------------------------------------------------------------------------

class TestCheckSubjectCentering:

    def test_corner_subject_is_degraded(self):
        img = _make_small_subject_image()
        result = check_subject_centering(img)
        assert result["status"] == "degraded"
        assert V_SUBJECT_OFFCENTER in result["violations"]
        assert result["offset_ratio"] > THRESHOLD_SUBJECT_OFFSET

    def test_centered_subject_passes(self):
        img = _make_good_image()
        result = check_subject_centering(img)
        assert result["status"] == "passed"
        assert result["violations"] == []
        assert result["offset_ratio"] <= THRESHOLD_SUBJECT_OFFSET

    def test_no_subject_is_skipped(self):
        img = _make_no_subject_image()
        result = check_subject_centering(img)
        assert result["status"] == "skipped"
        assert result["violations"] == []

    def test_result_has_required_keys(self):
        img = _make_good_image()
        result = check_subject_centering(img)
        for key in ("status", "violations", "offset_ratio", "threshold_offset"):
            assert key in result


# ---------------------------------------------------------------------------
# Tests check_decor_dominance (H.4.5.1)
# ---------------------------------------------------------------------------

class TestCheckDecorDominance:

    def test_legacy_dominant_backdrop_no_longer_fools_gate3(self):
        """
        H.6.10 — La fixture historique (backdrop mid-gray + sujet minuscule)
        ne déclenche PLUS decor_dominates : la segmentation relative isole
        correctement le sujet (fg_ratio ≈ 0.001), le gate 3 ferme. La
        pathologie réelle est désormais diagnostiquée par subject_too_small
        (cf. TestRunVisualQAFalseNegativeFixture).
        """
        img = _make_dominant_backdrop_image()
        result = check_decor_dominance(img)
        assert result["status"] == "passed"
        assert result["foreground_area_ratio"] < THRESHOLD_FOREGROUND_DOMINANT

    def test_blob_dominant_image_is_degraded(self):
        """H.6.10 — nappe claire full-frame avec périphérie sombre : le
        triple gate reste capable de détecter un décor dominant légitime."""
        img = _make_blob_dominant_image()
        result = check_decor_dominance(img)
        assert result["status"] == "degraded"
        assert V_DECOR_DOMINATES in result["violations"]
        assert result["dominant_ratio"] > THRESHOLD_DECOR_DOMINANCE
        assert result["foreground_area_ratio"] > THRESHOLD_FOREGROUND_DOMINANT

    def test_minimalist_packshot_stays_passed(self):
        """
        Packshot minimaliste (fond sombre uniforme + sujet large centré) :
        gate 3 (foreground_area_ratio) bloque la dégradation malgré bin dominant.
        """
        img = _make_minimalist_packshot_image()
        result = check_decor_dominance(img)
        assert result["status"] == "passed", (
            f"Packshot minimaliste ne doit PAS être degraded "
            f"(dom={result['dominant_ratio']}, fg={result['foreground_area_ratio']})"
        )
        assert result["violations"] == []
        assert result["foreground_area_ratio"] < THRESHOLD_FOREGROUND_DOMINANT

    def test_good_image_passes(self):
        """Image avec sujet centré et fond varié → distribution étalée → passed."""
        img = _make_good_image()
        result = check_decor_dominance(img)
        assert result["status"] == "passed"
        assert result["violations"] == []

    def test_monochrome_is_not_decor_degraded(self):
        """Image monochrome : gate 2 (std) ferme. luminance_contrast la flag, pas decor_dominance."""
        img = _make_uniform_image(20)
        result = check_decor_dominance(img)
        assert result["status"] == "passed"
        assert V_DECOR_DOMINATES not in result["violations"]

    def test_dominant_ratio_exposed(self):
        img = _make_dominant_backdrop_image()
        result = check_decor_dominance(img)
        assert "dominant_ratio" in result
        assert isinstance(result["dominant_ratio"], float)

    def test_foreground_area_ratio_exposed(self):
        img = _make_dominant_backdrop_image()
        result = check_decor_dominance(img)
        assert "foreground_area_ratio" in result
        assert isinstance(result["foreground_area_ratio"], float)

    def test_thresholds_exposed(self):
        img = _make_dominant_backdrop_image()
        result = check_decor_dominance(img)
        assert result["threshold_dominance"] == THRESHOLD_DECOR_DOMINANCE
        assert result["threshold_foreground_dominant"] == THRESHOLD_FOREGROUND_DOMINANT

    def test_degraded_result_has_details(self):
        img = _make_blob_dominant_image()
        result = check_decor_dominance(img)
        assert result.get("details") is not None
        assert "décor" in result["details"].lower() or "decor" in result["details"].lower()


# ---------------------------------------------------------------------------
# Tests bbox_gradient_v1 — référence de fond par colonne (backdrop dégradé)
# ---------------------------------------------------------------------------

class TestBboxGradientV1:
    """Résidu H.6.10 : bbox imprécise sur backdrop en dégradé latéral.
    Cas de référence réels : 80358b99 / c4076a23 (cf. cadrage 09)."""

    def test_column_reference_captures_lateral_gradient(self):
        """La référence par colonne est plus sombre aux bords qu'au centre."""
        img = _make_gradient_backdrop_image()
        w = img.size[0]
        ref = _background_column_reference(img)
        assert ref is not None
        assert len(ref) == w
        assert ref[0] < ref[w // 2]
        assert ref[-1] < ref[w // 2]

    def test_global_median_glues_to_edge_but_per_column_does_not(self):
        """Preuve du correctif : la médiane GLOBALE colle la bbox au bord
        sombre ; la référence PAR COLONNE la recentre."""
        img = _make_gradient_backdrop_image()
        w, _ = img.size

        # Ancienne segmentation (médiane de fond globale) : bbox collée à gauche.
        gmed = _background_median(img)
        global_mask = img.point(
            lambda p: 255 if abs(p - gmed) > FOREGROUND_DELTA else 0
        )
        gbbox = global_mask.getbbox()
        assert gbbox[0] == 0, "la médiane globale doit coller la bbox au bord (bug V1)"

        # Nouvelle segmentation (par colonne) : bbox décollée du bord.
        pbbox = _foreground_mask(img).getbbox()
        assert pbbox[0] >= w // 5, f"bbox toujours collée au bord : {pbbox}"

    def test_gradient_bbox_is_centered_not_glued(self):
        img = _make_gradient_backdrop_image()
        w, h = img.size
        bbox = check_subject_bbox_detected(img)["bbox"]
        assert bbox is not None
        left, top, right, bottom = bbox
        # Le sujet réel est dans le tiers central — la bbox ne touche pas le bord.
        assert left >= w // 5
        assert right <= w - w // 5

    def test_gradient_area_ratio_reflects_subject_not_full_width(self):
        """area_ratio doit refléter le sujet (≈ centre), pas la pleine largeur."""
        img = _make_gradient_backdrop_image()
        area = check_subject_area_ratio(img)["subject_area_ratio"]
        assert area >= THRESHOLD_SUBJECT_AREA   # sujet bien détecté
        assert area < 0.25                       # plus de bbox pleine largeur

    def test_gradient_centering_passes(self):
        img = _make_gradient_backdrop_image()
        assert check_subject_centering(img)["status"] == "passed"

    def test_gradient_decor_dominance_not_regressed(self):
        """Le gate decor reste passed (le bord sombre n'est plus full-frame)."""
        img = _make_gradient_backdrop_image()
        assert check_decor_dominance(img)["status"] == "passed"

    def test_run_visual_qa_reports_per_column_method(self):
        img = _make_gradient_backdrop_image()
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "preview.png"
            _save_png(img, p)
            result = run_visual_qa(str(p))
        assert result["segmentation"] == SEGMENTATION_METHOD_V2

    def test_flat_background_unchanged_and_uses_v2(self):
        """Non-régression : sur fond plat, la bbox du sujet centré est
        inchangée et la méthode reportée est bien la v2."""
        img = _make_good_image()
        bbox = check_subject_bbox_detected(img)["bbox"]
        assert bbox is not None
        # Sujet 80×80 centré sur 128×128 → bbox ≈ [24,24,104,104].
        left, top, right, bottom = bbox
        assert 20 <= left <= 28 and 20 <= top <= 28
        assert 104 <= right <= 108 and 104 <= bottom <= 108
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "preview.png"
            _save_png(img, p)
            assert run_visual_qa(str(p))["segmentation"] == SEGMENTATION_METHOD_V2


# ---------------------------------------------------------------------------
# Tests run_visual_qa
# ---------------------------------------------------------------------------

class TestRunVisualQA:

    def test_none_render_path_returns_skipped(self):
        result = run_visual_qa(None)
        assert result["status"] == "skipped"
        assert result["violations"] == []
        assert "checks" in result

    def test_nonexistent_path_returns_skipped(self):
        result = run_visual_qa("/nonexistent/path/preview.png")
        assert result["status"] == "skipped"
        assert result["violations"] == []

    def test_all_checks_present_in_result(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "preview.png"
            _save_png(_make_good_image(), p)
            result = run_visual_qa(str(p))
        for key in ("luminance_contrast", "subject_bbox_detected",
                    "subject_area_ratio", "subject_centering",
                    "decor_dominance"):
            assert key in result["checks"], f"Check manquant : {key}"

    def test_top_level_keys_present(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "preview.png"
            _save_png(_make_good_image(), p)
            result = run_visual_qa(str(p))
        for key in ("status", "violations", "checks"):
            assert key in result

    def test_good_image_passes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "preview.png"
            _save_png(_make_good_image(), p)
            result = run_visual_qa(str(p))
        assert result["status"] == "passed"
        assert result["violations"] == []

    def test_uniform_image_is_degraded(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "preview.png"
            _save_png(_make_uniform_image(20), p)
            result = run_visual_qa(str(p))
        assert result["status"] == "degraded"
        assert V_LOW_CONTRAST in result["violations"]

    def test_small_corner_subject_is_degraded(self):
        """Sujet petit en bas-gauche → degraded (subject_area_ratio et/ou subject_centering)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "preview.png"
            _save_png(_make_small_subject_image(), p)
            result = run_visual_qa(str(p))
        assert result["status"] == "degraded"
        assert any(v in result["violations"] for v in (V_SUBJECT_TOO_SMALL, V_SUBJECT_OFFCENTER))

    def test_no_subject_is_degraded(self):
        """Aucun pixel sujet → subject_out_of_frame."""
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "preview.png"
            _save_png(_make_no_subject_image(), p)
            result = run_visual_qa(str(p))
        assert result["status"] == "degraded"
        assert V_SUBJECT_OUT_OF_FRAME in result["violations"]

    def test_skipped_when_render_path_none_no_violations(self):
        """render_path=None ne doit jamais produire de violations ni dégrader le status."""
        result = run_visual_qa(None)
        assert result["status"] == "skipped"
        assert result["violations"] == []
        # Tous les checks individuels en skipped
        for check_key, check_val in result["checks"].items():
            assert check_val["status"] == "skipped", (
                f"Check {check_key} devrait être skipped si render_path=None"
            )

    def test_violations_in_checks_consistent_with_top_level(self):
        """Les violations du top-level sont l'union de celles des checks."""
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "preview.png"
            _save_png(_make_small_subject_image(), p)
            result = run_visual_qa(str(p))
        all_from_checks: list[str] = []
        for check in result["checks"].values():
            all_from_checks.extend(check.get("violations", []))
        assert set(result["violations"]) == set(all_from_checks)


# ---------------------------------------------------------------------------
# Tests run_visual_qa — H.4.5.1 faux négatif decor_dominance
# ---------------------------------------------------------------------------

class TestRunVisualQAFalseNegativeFixture:
    """
    Vérifie le scénario H.4.5.1 sur fixtures sauvegardées en PNG :
    le faux négatif synthétique doit être catché, et le packshot minimaliste
    valide doit rester passed.
    """

    def test_dominant_backdrop_fixture_degrades_visual_qa(self):
        """H.6.10 — La pathologie « backdrop dominant + sujet minuscule »
        reste détectée, mais avec le BON diagnostic : subject_too_small
        (sujet correctement segmenté et mesuré minuscule), au lieu du
        decor_dominates produit par le masque leurré."""
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "preview.png"
            _save_png(_make_dominant_backdrop_image(), p)
            result = run_visual_qa(str(p))
        assert result["status"] == "degraded"
        assert V_SUBJECT_TOO_SMALL in result["violations"]
        assert result["checks"]["decor_dominance"]["status"] == "passed"

    def test_blob_dominant_fixture_degrades_visual_qa(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "preview.png"
            _save_png(_make_blob_dominant_image(), p)
            result = run_visual_qa(str(p))
        assert result["status"] == "degraded"
        assert V_DECOR_DOMINATES in result["violations"]

    def test_minimalist_packshot_fixture_stays_passed_via_qa(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "preview.png"
            _save_png(_make_minimalist_packshot_image(), p)
            result = run_visual_qa(str(p))
        assert result["status"] == "passed", (
            f"Packshot minimaliste ne doit PAS sortir degraded "
            f"(violations={result['violations']})"
        )
        assert V_DECOR_DOMINATES not in result["violations"]


# ---------------------------------------------------------------------------
# Tests intégration inspect_blend_scene + visual_qa
# ---------------------------------------------------------------------------

def _fake_bpy_report_ok() -> dict:
    return {
        "object_count": 3,
        "mesh_count": 1,
        "camera_count": 1,
        "light_count": 1,
        "has_active_camera": True,
        "object_names": ["Main_Subject", "Camera", "Key_Light"],
    }


def _make_proc_success(tmp_path: Path, bpy_report: dict):
    def _side_effect(cmd, **kwargs):
        import re
        script_path = cmd[-1]
        try:
            content = Path(script_path).read_text(encoding="utf-8")
            m = re.search(r"open\((.+?),", content)
            if m:
                report_path = m.group(1).strip().strip("'\"")
                Path(report_path).write_text(json.dumps(bpy_report), encoding="utf-8")
        except Exception:
            pass
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = ""
        mock_proc.stderr = ""
        return mock_proc
    return _side_effect


class TestInspectBlendSceneVisualQA:

    def test_visual_qa_absent_when_no_render_path(self, tmp_path):
        """Sans render_path, visual_qa est skipped mais PRÉSENT dans le rapport."""
        blend = tmp_path / "scene.blend"
        blend.write_bytes(b"")
        scene_py = tmp_path / "scene.py"
        scene_py.write_text("import bpy", encoding="utf-8")

        bpy_report = _fake_bpy_report_ok()
        with patch("subprocess.run", side_effect=_make_proc_success(tmp_path, bpy_report)):
            report = inspect_blend_scene(
                exe="/fake/blender",
                output_path=str(blend),
                output_dir=str(tmp_path),
                timeout=30,
            )

        assert "visual_qa" in report
        assert report["visual_qa"]["status"] == "skipped"
        assert report["visual_qa"]["violations"] == []

    def test_visual_qa_present_with_good_render_path(self, tmp_path):
        """Avec une bonne preview, visual_qa est présent et passed."""
        blend = tmp_path / "scene.blend"
        blend.write_bytes(b"")
        scene_py = tmp_path / "scene.py"
        scene_py.write_text("import bpy", encoding="utf-8")

        preview = tmp_path / "preview.png"
        _save_png(_make_good_image(), preview)

        bpy_report = _fake_bpy_report_ok()
        with patch("subprocess.run", side_effect=_make_proc_success(tmp_path, bpy_report)):
            report = inspect_blend_scene(
                exe="/fake/blender",
                output_path=str(blend),
                output_dir=str(tmp_path),
                timeout=30,
                render_path=str(preview),
            )

        assert "visual_qa" in report
        assert report["visual_qa"]["status"] == "passed"
        assert report["status"] == "passed"

    def test_bad_preview_degrades_global_status(self, tmp_path):
        """Une preview quasi-monochrome doit dégrader scene_report.status."""
        blend = tmp_path / "scene.blend"
        blend.write_bytes(b"")
        scene_py = tmp_path / "scene.py"
        scene_py.write_text("import bpy", encoding="utf-8")

        preview = tmp_path / "preview.png"
        _save_png(_make_uniform_image(20), preview)

        bpy_report = _fake_bpy_report_ok()
        with patch("subprocess.run", side_effect=_make_proc_success(tmp_path, bpy_report)):
            report = inspect_blend_scene(
                exe="/fake/blender",
                output_path=str(blend),
                output_dir=str(tmp_path),
                timeout=30,
                render_path=str(preview),
            )

        assert report["status"] == "degraded"
        assert V_LOW_CONTRAST in report["violations"]
        assert report["visual_qa"]["status"] == "degraded"

    def test_pixel_framing_is_signal_only(self, tmp_path):
        """V1.1b — sujet minuscule en coin : les checks PIXEL de cadrage
        (too_small / offcenter) restent émis dans visual_qa (diagnostic
        perceptuel) mais N'ESCALADENT PLUS le status. L'autorité de cadrage est
        framing_contract (ici skipped, faute de géométrie → cadrage non enforced)."""
        blend = tmp_path / "scene.blend"
        blend.write_bytes(b"")
        scene_py = tmp_path / "scene.py"
        scene_py.write_text("import bpy", encoding="utf-8")

        preview = tmp_path / "preview.png"
        _save_png(_make_small_subject_image(), preview)

        bpy_report = _fake_bpy_report_ok()   # pas de framing_raw → contrat skipped
        with patch("subprocess.run", side_effect=_make_proc_success(tmp_path, bpy_report)):
            report = inspect_blend_scene(
                exe="/fake/blender",
                output_path=str(blend),
                output_dir=str(tmp_path),
                timeout=30,
                render_path=str(preview),
            )

        # Les violations pixel de cadrage sont dans le bloc visual_qa (signal)…
        vq_violations = report["visual_qa"]["violations"]
        assert any(v in vq_violations for v in (V_SUBJECT_TOO_SMALL, V_SUBJECT_OFFCENTER))
        # …mais filtrées du rapport global décisionnel → status non dégradé par elles.
        assert not any(v in report["violations"] for v in (
            V_SUBJECT_TOO_SMALL, V_SUBJECT_OUT_OF_FRAME, V_SUBJECT_OFFCENTER))
        assert report["status"] == "passed"

    def test_visual_qa_present_in_early_failure_path(self, tmp_path):
        """Même si le .blend est absent, visual_qa est présent dans le rapport (skipped)."""
        missing_blend = tmp_path / "scene.blend"  # non créé

        report = inspect_blend_scene(
            exe="/fake/blender",
            output_path=str(missing_blend),
            output_dir=str(tmp_path),
            timeout=30,
        )

        assert report["status"] == "failed"
        assert "visual_qa" in report
        assert report["visual_qa"]["status"] == "skipped"

    def test_scene_report_json_contains_visual_qa(self, tmp_path):
        """Le fichier scene_report.json écrit sur disque doit contenir visual_qa."""
        blend = tmp_path / "scene.blend"
        blend.write_bytes(b"")
        scene_py = tmp_path / "scene.py"
        scene_py.write_text("import bpy", encoding="utf-8")

        preview = tmp_path / "preview.png"
        _save_png(_make_good_image(), preview)

        bpy_report = _fake_bpy_report_ok()
        with patch("subprocess.run", side_effect=_make_proc_success(tmp_path, bpy_report)):
            inspect_blend_scene(
                exe="/fake/blender",
                output_path=str(blend),
                output_dir=str(tmp_path),
                timeout=30,
                render_path=str(preview),
            )

        scene_report_json = tmp_path / "scene_report.json"
        assert scene_report_json.exists()
        data = json.loads(scene_report_json.read_text(encoding="utf-8"))
        assert "visual_qa" in data
        assert "checks" in data["visual_qa"]
        for check_key in ("luminance_contrast", "subject_bbox_detected",
                          "subject_area_ratio", "subject_centering",
                          "decor_dominance"):
            assert check_key in data["visual_qa"]["checks"]

    def test_dominant_backdrop_preview_degrades_global_status(self, tmp_path):
        """H.4.5.1 / V1.1b — backdrop dominant (sujet trop petit). Le diagnostic
        PIXEL (subject_too_small, H.6.10) est désormais signal-only ; l'autorité
        de cadrage framing_contract dégrade le status sur la géométrie projetée."""
        blend = tmp_path / "scene.blend"
        blend.write_bytes(b"")
        scene_py = tmp_path / "scene.py"
        scene_py.write_text("import bpy", encoding="utf-8")

        preview = tmp_path / "preview.png"
        _save_png(_make_dominant_backdrop_image(), preview)

        bpy_report = _fake_bpy_report_ok()
        bpy_report["framing_raw"] = _framing_raw(0.02)   # occupation projetée trop faible
        with patch("subprocess.run", side_effect=_make_proc_success(tmp_path, bpy_report)):
            report = inspect_blend_scene(
                exe="/fake/blender",
                output_path=str(blend),
                output_dir=str(tmp_path),
                timeout=30,
                render_path=str(preview),
            )

        # Autorité = contrat projeté : framing_occupancy_out dégrade le status.
        assert report["status"] == "degraded"
        assert framing_contract.V_FRAMING_OCCUPANCY_OUT in report["violations"]
        # decor_dominance ne se déclenche pas à tort (H.6.10) ; le diagnostic
        # pixel subject_too_small reste signal-only (hors rapport global).
        assert report["visual_qa"]["checks"]["decor_dominance"]["status"] == "passed"
        assert V_SUBJECT_TOO_SMALL not in report["violations"]

    def test_minimalist_packshot_preview_stays_passed(self, tmp_path):
        """H.4.5.1 — Packshot minimaliste valide ne doit pas être degraded à tort."""
        blend = tmp_path / "scene.blend"
        blend.write_bytes(b"")
        scene_py = tmp_path / "scene.py"
        scene_py.write_text("import bpy", encoding="utf-8")

        preview = tmp_path / "preview.png"
        _save_png(_make_minimalist_packshot_image(), preview)

        bpy_report = _fake_bpy_report_ok()
        with patch("subprocess.run", side_effect=_make_proc_success(tmp_path, bpy_report)):
            report = inspect_blend_scene(
                exe="/fake/blender",
                output_path=str(blend),
                output_dir=str(tmp_path),
                timeout=30,
                render_path=str(preview),
            )

        assert report["status"] == "passed", (
            f"Packshot minimaliste ne doit pas être degraded "
            f"(violations={report['violations']})"
        )
        assert V_DECOR_DOMINATES not in report["violations"]


# ---------------------------------------------------------------------------
# framing_contract (§9.2, Décision 17) — intégration dans inspect_blend_scene
# ---------------------------------------------------------------------------

def _framing_raw(half_y: float) -> dict:
    """Données brutes de cadrage : caméra à (0,0,2) regardant −Z, sujet centré
    de demi-hauteur half_y (occupation pilotée par half_y)."""
    vm = framing_contract.view_matrix_from_pose((0.0, 0.0, 2.0), (0.0, 0.0, 0.0))
    corners = [[dx, dy, dz]
               for dx in (-0.05, 0.05) for dy in (-half_y, half_y) for dz in (-0.05, 0.05)]
    return {
        "camera": {"view_matrix": [list(r) for r in vm], "lens": 50.0,
                   "sensor_width": 36.0, "sensor_height": 24.0, "sensor_fit": "AUTO",
                   "shift_x": 0.0, "shift_y": 0.0},
        "render": {"res_x": 512, "res_y": 512, "pixel_x": 1.0, "pixel_y": 1.0},
        "subject_corners": corners,
    }


class TestFramingContractIntegration:

    def _run(self, tmp_path, bpy_report):
        blend = tmp_path / "scene.blend"
        blend.write_bytes(b"")
        (tmp_path / "scene.py").write_text("import bpy", encoding="utf-8")
        preview = tmp_path / "preview.png"
        _save_png(_make_good_image(), preview)
        with patch("subprocess.run", side_effect=_make_proc_success(tmp_path, bpy_report)):
            return inspect_blend_scene(
                exe="/fake/blender", output_path=str(blend),
                output_dir=str(tmp_path), timeout=30, render_path=str(preview),
            )

    def test_skipped_without_framing_raw(self, tmp_path):
        report = self._run(tmp_path, _fake_bpy_report_ok())
        assert "framing_contract" in report
        assert report["framing_contract"]["status"] == "skipped"

    def test_computed_when_well_framed(self, tmp_path):
        bpy_report = _fake_bpy_report_ok()
        bpy_report["framing_raw"] = _framing_raw(0.288)   # occupation ≈ 0.4
        report = self._run(tmp_path, bpy_report)
        block = report["framing_contract"]
        assert block["status"] == "passed"
        assert block["method"] == "projected_ndc_v1"
        assert "screen_bbox" in block
        assert "framing_divergence" in block

    def test_framing_contract_escalates_status(self, tmp_path):
        # V1.1b — AUTORITÉ DE CADRAGE TRANSFÉRÉE : une violation framing_* du
        # contrat projeté escalade désormais le status global (≠ V1 signal-only).
        bpy_report = _fake_bpy_report_ok()
        bpy_report["framing_raw"] = _framing_raw(0.02)    # sujet trop petit
        report = self._run(tmp_path, bpy_report)
        block = report["framing_contract"]
        assert block["status"] == "degraded"
        assert framing_contract.V_FRAMING_OCCUPANCY_OUT in block["violations"]
        # La violation framing_* est dans le rapport global décisionnel…
        assert framing_contract.V_FRAMING_OCCUPANCY_OUT in report["violations"]
        # …et dégrade le status, malgré une preview correcte (autorité géométrique).
        assert report["status"] == "degraded"

    def test_divergence_is_signal_only_block(self, tmp_path):
        bpy_report = _fake_bpy_report_ok()
        bpy_report["framing_raw"] = _framing_raw(0.288)
        report = self._run(tmp_path, bpy_report)
        div = report["framing_contract"]["framing_divergence"]
        assert "diverged" in div and "iou" in div
