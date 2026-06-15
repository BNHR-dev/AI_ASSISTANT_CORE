"""
Tests — framing_contract (contrat de cadrage par projection §9.2, V1).

Deux familles :
  - tests PURS (toujours exécutés) : projection NDC, invariants, divergence ;
  - test ORACLE (skip si Blender absent) : le module pur s'accorde avec
    bpy_extras.world_to_camera_view sur la caméra canonique.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from app.engine import framing_contract as fc
from app.engine.product_render_builder import SUBJECT_GEOMETRY, _subject_location
from app.engine.blender_runtime_corrector import CANONICAL_CAMERA

IDENTITY = (
    (1.0, 0.0, 0.0, 0.0),
    (0.0, 1.0, 0.0, 0.0),
    (0.0, 0.0, 1.0, 0.0),
    (0.0, 0.0, 0.0, 1.0),
)


# ---------------------------------------------------------------------------
# Projection NDC — caméra identité (à l'origine, regardant −Z)
# ---------------------------------------------------------------------------

class TestProjection:
    def test_half_extents_square_lens50(self):
        hw, hh = fc.half_extents_at_unit_depth(50.0, 36.0, 24.0, "AUTO", 512, 512)
        assert hw == pytest.approx(0.36)
        assert hh == pytest.approx(0.36)

    def test_center_point_maps_to_center(self):
        u, v, z = fc.project_point(IDENTITY, 0.36, 0.36, (0.0, 0.0, -2.0))
        assert u == pytest.approx(0.5)
        assert v == pytest.approx(0.5)
        assert z == pytest.approx(2.0)  # profondeur positive devant

    def test_right_and_top_edges(self):
        # point au bord droit du frame à profondeur 2 : x = half_w * 2
        u, _, _ = fc.project_point(IDENTITY, 0.36, 0.36, (0.72, 0.0, -2.0))
        assert u == pytest.approx(1.0)
        # point au bord haut : origine bas-gauche → v=1 en haut
        _, v, _ = fc.project_point(IDENTITY, 0.36, 0.36, (0.0, 0.72, -2.0))
        assert v == pytest.approx(1.0)

    def test_values_not_clamped(self):
        # point hors cadre → u > 1 (non clampé, conforme à la consigne)
        u, _, _ = fc.project_point(IDENTITY, 0.36, 0.36, (1.44, 0.0, -2.0))
        assert u > 1.0

    def test_point_behind_camera_has_negative_depth(self):
        _, _, z = fc.project_point(IDENTITY, 0.36, 0.36, (0.0, 0.0, 2.0))
        assert z < 0


# ---------------------------------------------------------------------------
# evaluate_framing — invariants V1
# ---------------------------------------------------------------------------

def _box_corners(cx, cy, cz, hx, hy, hz):
    return [(cx + dx, cy + dy, cz + dz)
            for dx in (-hx, hx) for dy in (-hy, hy) for dz in (-hz, hz)]


class TestEvaluateFraming:
    PROJ = {"half_w": 0.36, "half_h": 0.36}

    def test_well_framed_subject_passes(self):
        # sujet centré, occupation ≈ 0.4 à profondeur 2
        corners = _box_corners(0.0, 0.0, -2.0, 0.05, 0.288, 0.05)
        res = fc.evaluate_framing(IDENTITY, self.PROJ, corners)
        assert res["status"] == "passed"
        assert res["violations"] == []
        assert res["occupancy"] == pytest.approx(0.4, abs=0.02)
        assert res["center_u"] == pytest.approx(0.5, abs=0.01)
        assert res["in_frame"] is True

    def test_too_small_subject_flags_occupancy(self):
        corners = _box_corners(0.0, 0.0, -2.0, 0.02, 0.05, 0.02)
        res = fc.evaluate_framing(IDENTITY, self.PROJ, corners)
        assert fc.V_FRAMING_OCCUPANCY_OUT in res["violations"]
        assert res["status"] == "degraded"

    def test_offcenter_subject_flags_centering(self):
        corners = _box_corners(0.55, 0.0, -2.0, 0.05, 0.288, 0.05)
        res = fc.evaluate_framing(IDENTITY, self.PROJ, corners)
        assert fc.V_FRAMING_OFFCENTER in res["violations"]

    def test_out_of_frame_flags(self):
        # y=±0.7 à profondeur 2 → v ≈ 0.986 / 0.014, hors [0.05, 0.95]
        corners = _box_corners(0.0, 0.0, -2.0, 0.05, 0.70, 0.05)
        res = fc.evaluate_framing(IDENTITY, self.PROJ, corners)
        assert fc.V_FRAMING_OUT_OF_FRAME in res["violations"]
        assert res["in_frame"] is False

    def test_behind_camera_flags(self):
        corners = _box_corners(0.0, 0.0, 2.0, 0.05, 0.1, 0.05)
        res = fc.evaluate_framing(IDENTITY, self.PROJ, corners)
        assert fc.V_FRAMING_BEHIND_CAMERA in res["violations"]

    def test_empty_corners_skipped(self):
        res = fc.evaluate_framing(IDENTITY, self.PROJ, [])
        assert res["status"] == "skipped"


# ---------------------------------------------------------------------------
# framing_divergence — SIGNAL-ONLY (réconciliation projeté ↔ perçu)
# ---------------------------------------------------------------------------

class TestFramingDivergence:
    def test_ndc_to_top_left_fraction_inverts_y(self):
        # NDC origine bas-gauche → fraction origine haut-gauche : v haut → y top
        frac = fc.screen_bbox_to_top_left_fraction([0.25, 0.10, 0.75, 0.40])
        assert frac[0] == pytest.approx(0.25)   # left
        assert frac[2] == pytest.approx(0.75)   # right
        assert frac[1] == pytest.approx(1 - 0.40)  # top = 1 − v_haut
        assert frac[3] == pytest.approx(1 - 0.10)  # bottom

    def test_identical_bbox_iou_one(self):
        screen = [0.25, 0.25, 0.75, 0.75]
        perceptual = fc.screen_bbox_to_top_left_fraction(screen)
        d = fc.framing_divergence(screen, perceptual)
        assert d["iou"] == pytest.approx(1.0)
        assert d["diverged"] is False

    def test_disjoint_bbox_diverges(self):
        d = fc.framing_divergence([0.0, 0.0, 0.1, 0.1], [0.8, 0.8, 0.95, 0.95])
        assert d["iou"] == pytest.approx(0.0)
        assert d["diverged"] is True

    def test_no_perceptual_bbox_skipped(self):
        d = fc.framing_divergence([0.25, 0.25, 0.75, 0.75], None)
        assert d["status"] == "skipped"
        assert d["diverged"] is False


# ---------------------------------------------------------------------------
# Fixtures de non-régression : caméra + géométrie CANONIQUES (pur, sans Blender)
# ---------------------------------------------------------------------------

def _canonical_subject_corners(kind: str):
    g = SUBJECT_GEOMETRY[kind]
    loc = _subject_location(kind)
    r = g.get("radius", g.get("size", 0.1) / 2)
    if "half_h" in g:
        hz = g["half_h"]
    elif "depth" in g:
        hz = g["depth"] / 2.0
    elif "size" in g:
        hz = g["size"] / 2.0
    else:
        hz = r
    return _box_corners(loc[0], loc[1], loc[2], r, r, hz)


class TestCanonicalFixtures:
    def _eval(self, kind):
        vm = fc.view_matrix_from_pose(
            CANONICAL_CAMERA["location"], CANONICAL_CAMERA["rotation_euler"]
        )
        hw, hh = fc.half_extents_at_unit_depth(CANONICAL_CAMERA["lens"])
        return fc.evaluate_framing(vm, {"half_w": hw, "half_h": hh},
                                   _canonical_subject_corners(kind))

    def test_bottle_is_centered_and_in_front(self):
        res = self._eval("bottle")
        assert res["depth_min"] > 0                       # devant la caméra
        assert fc.CENTER_U_MIN <= res["center_u"] <= fc.CENTER_U_MAX
        assert res["in_frame"] is True

    def test_canonical_occupancy_below_target_documents_b1(self):
        # §9.2 / B1 : le cadrage canonique sous-cadre le sujet (occ < 0.25).
        # Ce test VERROUILLE le futur recalibrage : il devra repasser au vert.
        res = self._eval("bottle")
        assert res["occupancy"] < fc.OCCUPANCY_MIN
        assert fc.V_FRAMING_OCCUPANCY_OUT in res["violations"]


class TestOccupancyBand:
    def test_in_band_strict(self):
        assert fc.in_occupancy_band(fc.OCCUPANCY_MIN) is True
        assert fc.in_occupancy_band(0.40) is True
        assert fc.in_occupancy_band(fc.OCCUPANCY_MAX) is True

    def test_out_of_band_strict(self):
        # La conformité contrat ne connaît PAS la tolérance du correcteur :
        # 0.235 (montre ramenée, clampée) reste hors contrat.
        assert fc.in_occupancy_band(0.235) is False
        assert fc.in_occupancy_band(0.60) is False

    def test_occupancy_from_scene_matches_evaluate(self):
        # Source de géométrie unique : occupancy_from_scene == evaluate_framing.
        vm = fc.view_matrix_from_pose(
            CANONICAL_CAMERA["location"], CANONICAL_CAMERA["rotation_euler"]
        )
        hw, hh = fc.half_extents_at_unit_depth(CANONICAL_CAMERA["lens"])
        proj = {"half_w": hw, "half_h": hh}
        corners = _canonical_subject_corners("bottle")
        scalar = fc.occupancy_from_scene(vm, proj, corners)
        block = fc.evaluate_framing(vm, proj, corners)
        # block["occupancy"] est arrondi à 4 décimales ; même géométrie source.
        assert round(scalar, 4) == block["occupancy"]


# ---------------------------------------------------------------------------
# ORACLE — validation croisée contre world_to_camera_view (skip si Blender absent)
# ---------------------------------------------------------------------------

from app.clients.blender_client import resolve_blender_exe  # noqa: E402

_EXE = resolve_blender_exe()
_ORACLE_FIXTURE = Path(__file__).parent / "fixtures" / "framing_oracle.py"


@pytest.mark.skipif(_EXE is None, reason="Blender introuvable")
def test_pure_projection_matches_world_to_camera_view(tmp_path):
    out = tmp_path / "oracle.json"
    proc = subprocess.run(
        [_EXE, "--background", "--factory-startup", "--python",
         str(_ORACLE_FIXTURE), "--", str(out)],
        capture_output=True, text=True, timeout=120,
    )
    assert out.exists(), f"oracle non produit:\n{proc.stdout}\n{proc.stderr}"
    data = json.loads(out.read_text())

    vm = fc.view_matrix_from_pose(tuple(data["location"]), tuple(data["euler"]))
    hw, hh = fc.half_extents_at_unit_depth(data["lens"], 36.0, 24.0, "AUTO", 512, 512)

    for p, (ux, uy, uz) in zip(data["points"], data["wcv"]):
        u, v, z = fc.project_point(vm, hw, hh, tuple(p))
        assert u == pytest.approx(ux, abs=1e-3), f"u diverge sur {p}"
        assert v == pytest.approx(uy, abs=1e-3), f"v diverge sur {p}"
        assert z == pytest.approx(uz, abs=1e-3), f"z diverge sur {p}"
