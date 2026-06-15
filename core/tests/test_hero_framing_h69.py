"""
H.6.9 — hero_framing_v1 : projection géométrique, bornage caméra,
cap albédo backdrop, mesure de luminance fond par zones périphériques,
et intégration builder/corrector.

Tests purs : pas de Blender, pas d'I/O réseau. Pillow requis (déjà
dépendance de blender_qa_visual).
"""
from __future__ import annotations

import pytest

from app.engine.blender_runtime_corrector import (
    CANONICAL_CAMERA,
    CANONICAL_FILL_LIGHT,
    CANONICAL_KEY_LIGHT,
    CORRECTION_ADD_KEY_LIGHT,
    CORRECTION_NORMALIZE_CAMERA,
    CORRECTION_NORMALIZE_LIGHTING,
    CORRECTION_RERENDER_PREVIEW,
    build_correction_script,
)
from app.engine.hero_framing import (
    BACKDROP_ALBEDO_CAP,
    BACKGROUND_CLIPPED_LEVEL,
    HERO_DISTANCE_FACTOR_MAX,
    HERO_DISTANCE_FACTOR_MIN,
    HERO_FRAMING_REPORT_FILENAME,
    HERO_MIN_CAMERA_DISTANCE,
    HERO_OCCUPANCY_MAX,
    HERO_OCCUPANCY_MIN,
    HERO_OCCUPANCY_TOLERANCE,
    background_columns,
    background_luminance_stats,
    background_pixels,
    cap_backdrop_albedo,
    correction_outcome,
    hero_adjusted_distance,
    hero_distance_factor,
    is_clamped,
    occupancy_residual,
    requested_factor,
    target_occupancy_for,
    target_reached,
)
from app.engine.framing_contract import in_occupancy_band
from app.engine.product_render_builder import build_product_render_scene_script
from app.engine.product_render_ir import (
    BackdropIR,
    ProductRenderIntent,
    ProductSubjectIR,
)


def _make_ir(backdrop_color: str = "neutral_gray") -> ProductRenderIntent:
    return ProductRenderIntent(
        schema_version="v0",
        subject=ProductSubjectIR(kind="bottle", color="black", material="matte"),
        backdrop=BackdropIR(color=backdrop_color),
    )


# ---------------------------------------------------------------------------
# Politique de cible (V1.1a) — métrique NDC consommée en entrée
# ---------------------------------------------------------------------------
# Ces fonctions ne CALCULENT plus d'occupation (modèle vertical supprimé) :
# elles reçoivent un scalaire d'occupation NDC (framing_contract) et
# n'appliquent que la politique "retour à la borne violée".

class TestTargetPolicy:
    def test_in_band_is_strict_noop(self):
        for occ in (HERO_OCCUPANCY_MIN, 0.40, HERO_OCCUPANCY_MAX):
            assert target_occupancy_for(occ) is None
            assert hero_distance_factor(occ) == 1.0
            assert is_clamped(occ) is False

    def test_under_band_targets_min(self):
        occ = 0.20
        assert target_occupancy_for(occ) == HERO_OCCUPANCY_MIN
        assert requested_factor(occ) == pytest.approx(occ / HERO_OCCUPANCY_MIN)

    def test_over_band_targets_max(self):
        occ = 0.80
        assert target_occupancy_for(occ) == HERO_OCCUPANCY_MAX
        assert requested_factor(occ) == pytest.approx(occ / HERO_OCCUPANCY_MAX)

    def test_jar_borderline_triggers_no_more_dead_zone(self):
        # Baseline réelle : jar NDC 0.238 < 0.25 → corrige vers MIN. Le commit 1
        # supprime la dead-zone de tolérance AU DÉCLENCHEMENT (la tolérance ne
        # sert plus qu'à qualifier target_reached).
        assert target_occupancy_for(0.238) == HERO_OCCUPANCY_MIN
        assert hero_distance_factor(0.238) < 1.0

    def test_degenerate_is_noop(self):
        assert target_occupancy_for(0.0) is None
        assert target_occupancy_for(-1.0) is None
        assert hero_distance_factor(0.0) == 1.0


# ---------------------------------------------------------------------------
# Facteur de distance — bornage, clamp, minimalité
# ---------------------------------------------------------------------------

class TestDistanceFactor:
    def test_small_subject_moves_closer_clamped(self):
        # Montre baseline NDC 0.165 : demandé 0.165/0.25 = 0.66 < FACTOR_MIN
        # → clampé à FACTOR_MIN, cible non pleinement atteignable.
        occ = 0.165
        assert requested_factor(occ) == pytest.approx(occ / HERO_OCCUPANCY_MIN)
        assert hero_distance_factor(occ) == HERO_DISTANCE_FACTOR_MIN
        assert is_clamped(occ) is True

    def test_under_band_unclamped_within_factor_bounds(self):
        # jar 0.238 → 0.952 ∈ [FACTOR_MIN, FACTOR_MAX] → pas clampé.
        occ = 0.238
        assert hero_distance_factor(occ) == pytest.approx(requested_factor(occ))
        assert is_clamped(occ) is False

    def test_large_subject_moves_back_bounded(self):
        factor = hero_distance_factor(0.90)
        assert factor > 1.0
        assert factor <= HERO_DISTANCE_FACTOR_MAX
        assert hero_distance_factor(5.0) == HERO_DISTANCE_FACTOR_MAX
        assert is_clamped(5.0) is True

    def test_extreme_small_subject_clamped(self):
        assert hero_distance_factor(0.01) == HERO_DISTANCE_FACTOR_MIN

    def test_adjusted_distance_min_clamp(self):
        # Même avec un facteur < 1, jamais sous la distance minimale.
        assert hero_adjusted_distance(0.5, 0.01) == HERO_MIN_CAMERA_DISTANCE
        # Dans la bande → distance inchangée.
        assert hero_adjusted_distance(1.55, 0.40) == 1.55


# ---------------------------------------------------------------------------
# Qualification du résultat — résidu / cible atteinte (tolérance explicite)
# ---------------------------------------------------------------------------

class TestResultQualification:
    def test_target_reached_uses_explicit_tolerance(self):
        assert target_reached(HERO_OCCUPANCY_MIN, HERO_OCCUPANCY_MIN) is True
        # Strictement dans la tolérance → atteint ; nettement au-delà → non.
        assert target_reached(
            HERO_OCCUPANCY_MIN + HERO_OCCUPANCY_TOLERANCE / 2, HERO_OCCUPANCY_MIN
        ) is True
        assert target_reached(
            HERO_OCCUPANCY_MIN + 2 * HERO_OCCUPANCY_TOLERANCE, HERO_OCCUPANCY_MIN
        ) is False

    def test_residual_is_signed(self):
        assert occupancy_residual(0.23, 0.25) == pytest.approx(-0.02)
        assert occupancy_residual(0.27, 0.25) == pytest.approx(0.02)


# ---------------------------------------------------------------------------
# Sémantique de rapport (V1.1a) : champs de cible vs conformité contrat
# ---------------------------------------------------------------------------
# `target_reached` = convergence vers la cible à la tolérance du correcteur.
# `in_contract_band_after` = conformité STRICTE au contrat [MIN, MAX].
# La tolérance du correcteur ne doit jamais assouplir le contrat décisionnel.

class TestReportSemantics:
    def test_noop_keeps_target_fields_none(self):
        # Sujet en bande → pas de cible corrective : les trois champs restent
        # None (PAS target_reached=True : il n'y a rien à atteindre).
        out = correction_outcome(0.40, 0.40)
        assert out["target_occupancy"] is None
        assert out["occupancy_residual"] is None
        assert out["target_reached"] is None

    def test_jar_reaches_target_and_conforms_contract(self):
        # jar : 0.238 → 0.250. Cible atteinte ET dans le contrat strict.
        out = correction_outcome(0.238, 0.250)
        assert out["target_occupancy"] == HERO_OCCUPANCY_MIN
        assert out["target_reached"] is True
        assert in_occupancy_band(0.250) is True

    def test_watch_reaches_tolerance_but_violates_contract(self):
        # watch : 0.165 → 0.235 (clampé). À la tolérance 0.02 la cible 0.25 est
        # « atteinte », MAIS 0.235 < OCCUPANCY_MIN → hors contrat strict.
        out = correction_outcome(0.165, 0.235)
        assert out["target_reached"] is True
        assert in_occupancy_band(0.235) is False


# ---------------------------------------------------------------------------
# Exposition — énergies H.6.9 et cap albédo
# ---------------------------------------------------------------------------

class TestExposure:
    def test_h69_canonical_energies(self):
        """Verrouille la décision H.6.9 (calibrée par smokes 2026-06-11) :
        key 25 W, fill 10 W, ratio 2.5:1."""
        assert CANONICAL_KEY_LIGHT["energy"] == 25.0
        assert CANONICAL_FILL_LIGHT["energy"] == 10.0
        assert CANONICAL_KEY_LIGHT["energy"] / CANONICAL_FILL_LIGHT["energy"] == 2.5

    def test_cap_clamps_bright_backdrop(self):
        capped = cap_backdrop_albedo((0.95, 0.95, 0.95, 1.0))
        assert capped == (BACKDROP_ALBEDO_CAP, BACKDROP_ALBEDO_CAP, BACKDROP_ALBEDO_CAP, 1.0)

    def test_cap_preserves_dark_colors_and_alpha(self):
        assert cap_backdrop_albedo((0.5, 0.5, 0.5, 1.0)) == (0.5, 0.5, 0.5, 1.0)
        assert cap_backdrop_albedo((0.02, 0.9, 0.3, 0.8)) == (0.02, BACKDROP_ALBEDO_CAP, 0.3, 0.8)

    def test_builder_caps_white_backdrop(self):
        script = build_product_render_scene_script(_make_ir(backdrop_color="white"))
        # white = (0.95, 0.95, 0.95) → clampé au cap dans Backdrop_Material.
        assert f"({BACKDROP_ALBEDO_CAP}, {BACKDROP_ALBEDO_CAP}, {BACKDROP_ALBEDO_CAP}, 1.0)" in script
        assert "(0.95, 0.95, 0.95, 1.0)" not in script

    def test_builder_keeps_backdrop_under_cap(self):
        script = build_product_render_scene_script(_make_ir(backdrop_color="neutral_gray"))
        assert "(0.5, 0.5, 0.5, 1.0)" in script

    def test_builder_does_not_cap_subject_color(self):
        ir = ProductRenderIntent(
            schema_version="v0",
            subject=ProductSubjectIR(kind="bottle", color="white", material="matte"),
            backdrop=BackdropIR(color="black"),
        )
        script = build_product_render_scene_script(ir)
        # Le sujet white reste 0.95 : seul le backdrop est cappé.
        assert "(0.95, 0.95, 0.95, 1.0)" in script


# ---------------------------------------------------------------------------
# Mesure de luminance fond — zones périphériques
# ---------------------------------------------------------------------------

def _make_image(bg: int, subject: int | None = None, size: int = 64):
    from PIL import Image, ImageDraw
    img = Image.new("L", (size, size), color=bg)
    if subject is not None:
        draw = ImageDraw.Draw(img)
        # Sujet packshot : rectangle centré bas, hors zones périphériques.
        draw.rectangle((size // 3, size // 3, 2 * size // 3, size - 2), fill=subject)
    return img


class TestBackgroundLuminance:
    def test_peripheral_zones_exclude_centered_subject(self):
        # Fond sombre + sujet clair au centre : la mesure ne doit voir
        # que le fond (contrairement à _foreground_mask, leurré — B4).
        img = _make_image(bg=100, subject=255)
        pixels = background_pixels(img)
        assert pixels
        assert all(p == 100 for p in pixels)

    def test_stats_on_blown_background(self, tmp_path):
        path = tmp_path / "preview.png"
        _make_image(bg=252, subject=10).save(path)
        stats = background_luminance_stats(str(path))
        assert stats["status"] == "ok"
        assert stats["median"] == 252
        assert stats["clipped_ratio"] == 1.0
        assert stats["max"] == 252

    def test_p90_catches_partially_blown_background(self, tmp_path):
        # Fond majoritairement correct mais bande haute cramée : la médiane
        # passe, p90/clipped_ratio doivent alerter.
        from PIL import Image
        img = Image.new("L", (64, 64), color=140)
        for y in range(6):           # bande haute cramée (~9 % de la hauteur)
            for x in range(64):
                img.putpixel((x, y), 255)
        path = tmp_path / "preview.png"
        img.save(path)
        stats = background_luminance_stats(str(path))
        assert stats["status"] == "ok"
        assert 80 <= stats["median"] <= 210      # médiane dans la cible…
        assert stats["max"] >= BACKGROUND_CLIPPED_LEVEL  # …mais cramé détecté
        assert stats["clipped_ratio"] > 0.2

    def test_stats_missing_file_skipped(self):
        stats = background_luminance_stats("/nonexistent/preview.png")
        assert stats["status"] == "skipped"
        assert stats["median"] is None

    def test_background_columns_geometry(self):
        # bbox_gradient_v1 : une liste de fond par colonne, longueur = largeur.
        # Toute colonne a au moins ses pixels de bande haute ; les colonnes de
        # bord (latérales) en ont davantage que les colonnes centrales.
        from PIL import Image
        img = Image.new("L", (64, 64), color=120)
        cols = background_columns(img)
        assert len(cols) == 64
        assert all(c for c in cols)            # aucune colonne vide
        assert len(cols[0]) > len(cols[32])    # colonne de bord plus échantillonnée

    def test_background_columns_track_lateral_gradient(self):
        # Dégradé latéral : la médiane par colonne suit la luminance de la colonne.
        from PIL import Image
        w = 64
        img = Image.new("L", (w, 64))
        px = img.load()
        for x in range(w):
            for y in range(64):
                px[x, y] = 40 + x          # sombre à gauche, clair à droite
        cols = background_columns(img)
        med = lambda c: sorted(c)[len(c) // 2]
        assert med(cols[0]) < med(cols[w - 1])


# ---------------------------------------------------------------------------
# Intégration corrector — script de correction généré
# ---------------------------------------------------------------------------

class TestCorrectionScriptHeroBlock:
    def _normalization_script(self) -> str:
        return build_correction_script(
            "/tmp/scene.blend", "/tmp/preview.png",
            [CORRECTION_NORMALIZE_LIGHTING, CORRECTION_NORMALIZE_CAMERA,
             CORRECTION_RERENDER_PREVIEW],
        )

    def test_hero_block_imports_pure_modules_not_inlined(self):
        script = self._normalization_script()
        assert 'bpy.data.objects.get("Product_Subject")' in script
        assert "bound_box" in script
        # Anti-duplication : le script IMPORTE les modules purs par chemin
        # (même source que les tests), il ne réimplémente pas la formule.
        assert "_hf_sys.path.insert" in script
        assert "import framing_contract" in script
        assert "import hero_framing" in script
        assert "occupancy_from_scene" in script
        assert "target_occupancy_for" in script
        # Re-mesure NDC réelle après déplacement (plus d'occupancy analytique).
        assert "view_layer.update" in script

    def test_hero_report_written_next_to_blend(self):
        script = self._normalization_script()
        assert HERO_FRAMING_REPORT_FILENAME in script
        assert "/tmp/hero_framing.json" in script
        # Schéma de champs générique, stable pour le commit 2.
        for field in ("target_occupancy", "occupancy_residual",
                      "clamped", "target_reached", "factor_requested"):
            assert field in script

    def test_hero_block_absent_without_camera_correction(self):
        # add_key_light seul (sans reframe/normalize caméra) ne déclenche
        # pas le contrôle hero — il est attaché à la passe caméra.
        script = build_correction_script(
            "/tmp/scene.blend", "/tmp/preview.png",
            [CORRECTION_ADD_KEY_LIGHT],
        )
        assert "occupancy_from_scene" not in script
        assert "import hero_framing" not in script

    def test_canonical_camera_still_applied_first(self):
        # Le contrôle hero ajuste APRÈS la pose canonique : les deux blocs
        # doivent coexister, canonique d'abord.
        script = self._normalization_script()
        canonical_idx = script.index(str(CANONICAL_CAMERA["location"]))
        hero_idx = script.index("occupancy_from_scene")
        assert canonical_idx < hero_idx

    def test_generated_script_compiles(self):
        # Le script bpy généré doit être du Python syntaxiquement valide.
        compile(self._normalization_script(), "<correction_script>", "exec")
