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
    background_luminance_stats,
    background_pixels,
    cap_backdrop_albedo,
    hero_adjusted_distance,
    hero_distance_factor,
    projected_occupancy,
    visible_height_at,
)
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
# Projection géométrique
# ---------------------------------------------------------------------------

class TestProjection:
    def test_visible_height_smoke_geometry(self):
        # Caméra canonique (~1.55 m du sujet, lens 50, sensor 36) :
        # hauteur visible ~1.1 m — la base du calibrage H.6.9.
        assert visible_height_at(1.546, 50) == pytest.approx(1.113, abs=0.01)

    def test_occupancy_audit_smoke_1(self):
        # Smoke 1 audit : flacon rectangulaire h=0.308 m à ~1.53 m → ~28 %,
        # dans les bornes (proportions validées humainement → no-op caméra).
        occ = projected_occupancy(0.308, 1.53, 50)
        assert occ == pytest.approx(0.28, abs=0.01)
        assert hero_distance_factor(occ) == 1.0

    def test_occupancy_degenerate_inputs(self):
        assert projected_occupancy(0.3, 0.0, 50) == 0.0
        assert projected_occupancy(0.0, 1.5, 50) == 0.0
        assert visible_height_at(1.5, 0.0) == 0.0


# ---------------------------------------------------------------------------
# Facteur de distance — bornage et minimalité
# ---------------------------------------------------------------------------

class TestDistanceFactor:
    def test_within_bounds_is_noop(self):
        for occ in (HERO_OCCUPANCY_MIN, 0.40, HERO_OCCUPANCY_MAX):
            assert hero_distance_factor(occ) == 1.0

    def test_tolerance_band_is_noop(self):
        # Juste sous/au-dessus des bornes mais dans la tolérance → no-op
        # (l'ajustement ne se déclenche que sur violation significative).
        assert hero_distance_factor(HERO_OCCUPANCY_MIN - HERO_OCCUPANCY_TOLERANCE + 0.001) == 1.0
        assert hero_distance_factor(HERO_OCCUPANCY_MAX + HERO_OCCUPANCY_TOLERANCE - 0.001) == 1.0

    def test_small_subject_moves_camera_closer(self):
        occ = 0.15  # smoke 3 audit (chronomètre h=0.168 m)
        factor = hero_distance_factor(occ)
        assert factor < 1.0
        assert factor >= HERO_DISTANCE_FACTOR_MIN
        # L'occupation résultante ne dépasse jamais la borne basse :
        # on ramène À la borne, pas au-delà (mouvement minimal).
        assert occ / factor <= HERO_OCCUPANCY_MIN + 1e-9

    def test_large_subject_moves_camera_back_bounded(self):
        factor = hero_distance_factor(0.90)
        assert factor > 1.0
        assert factor <= HERO_DISTANCE_FACTOR_MAX
        # Cas extrême : clamp au facteur max, jamais plus.
        assert hero_distance_factor(5.0) == HERO_DISTANCE_FACTOR_MAX

    def test_extreme_small_subject_clamped(self):
        assert hero_distance_factor(0.01) == HERO_DISTANCE_FACTOR_MIN

    def test_degenerate_occupancy_is_noop(self):
        assert hero_distance_factor(0.0) == 1.0
        assert hero_distance_factor(-1.0) == 1.0

    def test_adjusted_distance_min_clamp(self):
        # Même avec un facteur < 1, jamais sous la distance minimale.
        assert hero_adjusted_distance(0.5, 0.01) == HERO_MIN_CAMERA_DISTANCE
        # Dans les bornes → distance inchangée.
        assert hero_adjusted_distance(1.55, 0.40) == 1.55


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

    def test_hero_block_present_with_camera_normalization(self):
        script = self._normalization_script()
        assert "hero_framing_v1" in script
        assert 'bpy.data.objects.get("Product_Subject")' in script
        assert "bound_box" in script
        # Constantes synchronisées avec le module pur.
        assert str(HERO_OCCUPANCY_MIN) in script
        assert str(HERO_DISTANCE_FACTOR_MIN) in script
        assert str(HERO_MIN_CAMERA_DISTANCE) in script

    def test_hero_report_written_next_to_blend(self):
        script = self._normalization_script()
        assert HERO_FRAMING_REPORT_FILENAME in script
        assert "/tmp/hero_framing.json" in script

    def test_hero_block_absent_without_camera_correction(self):
        # add_key_light seul (sans reframe/normalize caméra) ne déclenche
        # pas le contrôle hero — il est attaché à la passe caméra.
        script = build_correction_script(
            "/tmp/scene.blend", "/tmp/preview.png",
            [CORRECTION_ADD_KEY_LIGHT],
        )
        assert "hero_framing_v1" not in script

    def test_canonical_camera_still_applied_first(self):
        # Le contrôle hero ajuste APRÈS la pose canonique : les deux blocs
        # doivent coexister, canonique d'abord.
        script = self._normalization_script()
        canonical_idx = script.index(str(CANONICAL_CAMERA["location"]))
        hero_idx = script.index("hero_framing_v1")
        assert canonical_idx < hero_idx

    def test_generated_script_compiles(self):
        # Le script bpy généré doit être du Python syntaxiquement valide.
        compile(self._normalization_script(), "<correction_script>", "exec")
