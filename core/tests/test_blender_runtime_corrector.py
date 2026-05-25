"""
Tests unitaires — blender_runtime_corrector (H.4.8).

Tests purs (plan_corrections, build_correction_script) + tests mockés
(apply_corrections via subprocess.run patché). Aucun Blender réel requis.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.engine.blender_runtime_corrector import (
    CANONICAL_CAMERA,
    CANONICAL_FILL_LIGHT,
    CANONICAL_KEY_LIGHT,
    CORRECTION_ADD_FILL_LIGHT,
    CORRECTION_ADD_KEY_LIGHT,
    CORRECTION_REFRAME_CAMERA,
    CORRECTION_REMOVE_SUN,
    CORRECTION_RERENDER_PREVIEW,
    REQUIRED_SUBJECT_NAME,
    apply_corrections,
    build_correction_script,
    plan_corrections,
)


# ---------------------------------------------------------------------------
# plan_corrections — fonction pure
# ---------------------------------------------------------------------------

class TestPlanCorrections:

    def test_skipped_when_template_not_product_render(self):
        plan = plan_corrections("interior_space", ["Product_Subject"], [])
        assert plan["applicable"] is False
        assert plan["reason"] == "template_not_product_render"
        assert plan["corrections"] == []

    def test_skipped_when_template_none(self):
        plan = plan_corrections(None, ["Product_Subject"], [])
        assert plan["applicable"] is False
        assert plan["reason"] == "template_not_product_render"

    def test_skipped_when_no_product_subject(self):
        plan = plan_corrections(
            "product_render",
            ["Backdrop_Plane", "Pedestal", "Camera", "Sun"],
            ["template_required_missing:Product_Subject"],
        )
        assert plan["applicable"] is False
        assert plan["reason"] == "no_product_subject"
        assert plan["corrections"] == []

    def test_skipped_when_object_names_empty(self):
        plan = plan_corrections("product_render", [], [])
        assert plan["applicable"] is False
        assert plan["reason"] == "no_object_names"

    def test_skipped_when_object_names_none(self):
        plan = plan_corrections("product_render", None, [])
        assert plan["applicable"] is False

    def test_no_corrections_needed_when_full_contract(self):
        """Cas nominal : tout est déjà là, rien à corriger."""
        plan = plan_corrections(
            "product_render",
            ["Backdrop_Plane", "Pedestal", "Product_Subject",
             "Camera", "Key_Light", "Fill_Light"],
            [],
        )
        assert plan["applicable"] is True
        assert plan["reason"] == "no_corrections_needed"
        assert plan["corrections"] == []

    def test_plan_smoke_h47_state(self):
        """État réel du smoke H.4.7 : Key/Fill manquants + Sun présent."""
        plan = plan_corrections(
            "product_render",
            ["Backdrop_Plane", "Pedestal", "Product_Subject", "Camera", "Sun"],
            [],
        )
        assert plan["applicable"] is True
        assert plan["reason"] is None
        assert CORRECTION_REMOVE_SUN in plan["corrections"]
        assert CORRECTION_ADD_KEY_LIGHT in plan["corrections"]
        assert CORRECTION_ADD_FILL_LIGHT in plan["corrections"]
        assert CORRECTION_REFRAME_CAMERA in plan["corrections"]
        assert CORRECTION_RERENDER_PREVIEW in plan["corrections"]

    def test_plan_add_only_fill_light(self):
        plan = plan_corrections(
            "product_render",
            ["Backdrop_Plane", "Pedestal", "Product_Subject",
             "Camera", "Key_Light"],
            [],
        )
        assert plan["applicable"] is True
        assert CORRECTION_ADD_FILL_LIGHT in plan["corrections"]
        assert CORRECTION_ADD_KEY_LIGHT not in plan["corrections"]
        assert CORRECTION_REMOVE_SUN not in plan["corrections"]
        # Cadrage caméra + re-rendu attachés par défaut quand une correction structurelle est planifiée
        assert CORRECTION_REFRAME_CAMERA in plan["corrections"]
        assert CORRECTION_RERENDER_PREVIEW in plan["corrections"]


# ---------------------------------------------------------------------------
# build_correction_script — fonction pure
# ---------------------------------------------------------------------------

class TestBuildCorrectionScript:

    def test_script_starts_with_import_bpy(self):
        script = build_correction_script(
            "/tmp/scene.blend", "/tmp/preview.png",
            [CORRECTION_REFRAME_CAMERA],
        )
        assert script.startswith("import bpy")

    def test_script_includes_save_as_mainfile_with_blend_path(self):
        script = build_correction_script(
            "/tmp/x/scene.blend", "/tmp/x/preview.png",
            [CORRECTION_REFRAME_CAMERA],
        )
        assert "wm.save_as_mainfile" in script
        assert "/tmp/x/scene.blend" in script

    def test_add_key_light_uses_canonical_params(self):
        script = build_correction_script(
            "/tmp/scene.blend", "/tmp/preview.png",
            [CORRECTION_ADD_KEY_LIGHT],
        )
        assert "Key_Light" in script
        assert "AREA" in script
        assert str(CANONICAL_KEY_LIGHT["energy"]) in script
        assert str(CANONICAL_KEY_LIGHT["size"]) in script

    def test_add_fill_light_uses_canonical_params(self):
        script = build_correction_script(
            "/tmp/scene.blend", "/tmp/preview.png",
            [CORRECTION_ADD_FILL_LIGHT],
        )
        assert "Fill_Light" in script
        assert str(CANONICAL_FILL_LIGHT["energy"]) in script

    def test_remove_sun_uses_objects_remove(self):
        script = build_correction_script(
            "/tmp/scene.blend", "/tmp/preview.png",
            [CORRECTION_REMOVE_SUN],
        )
        assert "Sun" in script
        assert "objects.remove" in script

    def test_reframe_camera_uses_canonical_lens(self):
        script = build_correction_script(
            "/tmp/scene.blend", "/tmp/preview.png",
            [CORRECTION_REFRAME_CAMERA],
        )
        assert f'data.lens = {CANONICAL_CAMERA["lens"]}' in script

    def test_canonical_camera_packshot_lens_range(self):
        """H.4.8.1 — empêche une régression vers une focale téléobjectif.

        En H.4.8 initial la focale était 80mm (téléobjectif), produisant un
        cadrage trop serré qui coupait le sujet. Pour un packshot lisible,
        une focale standard (≈ 35–60mm) est nécessaire.
        """
        lens = CANONICAL_CAMERA["lens"]
        assert 35 <= lens <= 60, (
            f"CANONICAL_CAMERA.lens = {lens}mm hors plage packshot raisonnable "
            f"(attendu 35-60mm). Une focale plus longue produit un cadrage "
            f"trop zoomé et coupe le sujet (régression H.4.8.1)."
        )

    def test_canonical_camera_minimum_distance_to_origin(self):
        """H.4.8.1 — empêche un cadrage trop proche.

        La caméra doit être à au moins ~1.4m de l'origine pour permettre au
        sujet contractuel (Product_Subject ~0.3m de hauteur) d'apparaître
        entier dans le frame avec la focale canonique.
        """
        x, y, z = CANONICAL_CAMERA["location"]
        distance = (x * x + y * y + z * z) ** 0.5
        assert distance >= 1.4, (
            f"CANONICAL_CAMERA.location distance origine = {distance:.2f}m "
            f"insuffisante pour un cadrage packshot lisible (attendu >= 1.4m)."
        )

    def test_canonical_camera_aims_above_origin(self):
        """H.4.8.1 — la caméra doit viser au-dessus de l'origine pour centrer
        le couple Pedestal + Product_Subject (centre vertical ~z=0.175)
        plutôt que la base du socle (z=0).

        On vérifie que le pitch X est légèrement au-dessus de π/2 (~1.571),
        soit une légère plongée pour viser un point en hauteur depuis l'avant.
        """
        rx = CANONICAL_CAMERA["rotation_euler"][0]
        # Plage acceptable : 1.20 (très peu de plongée) à 1.40 (plongée marquée).
        # Plus bas = caméra plus horizontale ; plus haut = caméra plus plongeante.
        assert 1.20 <= rx <= 1.40, (
            f"CANONICAL_CAMERA.rotation_euler.x = {rx:.3f} hors plage attendue "
            f"(1.20-1.40 rad). Ce paramètre contrôle la hauteur de visée."
        )

    def test_rerender_includes_render_call(self):
        script = build_correction_script(
            "/tmp/scene.blend", "/tmp/preview.png",
            [CORRECTION_REFRAME_CAMERA, CORRECTION_RERENDER_PREVIEW],
        )
        assert "render.render(write_still=True)" in script
        assert "/tmp/preview.png" in script
        assert "EEVEE" in script  # branche moteur

    def test_no_rerender_when_render_path_none(self):
        script = build_correction_script(
            "/tmp/scene.blend", None,
            [CORRECTION_REFRAME_CAMERA, CORRECTION_RERENDER_PREVIEW],
        )
        # Pas de bloc render
        assert "render.render(" not in script

    def test_script_has_no_llm_call(self):
        """Sécurité : la passe corrective ne doit JAMAIS faire d'appel LLM."""
        script = build_correction_script(
            "/tmp/scene.blend", "/tmp/preview.png",
            [CORRECTION_REMOVE_SUN, CORRECTION_ADD_KEY_LIGHT,
             CORRECTION_ADD_FILL_LIGHT, CORRECTION_REFRAME_CAMERA,
             CORRECTION_RERENDER_PREVIEW],
        )
        forbidden_tokens = ("ollama", "openai", "anthropic", "requests.post", "urllib", "http")
        for tok in forbidden_tokens:
            assert tok not in script.lower(), f"Token interdit trouvé dans le script : {tok}"

    def test_full_smoke_h47_script_contains_all_expected_ops(self):
        script = build_correction_script(
            "/tmp/scene.blend", "/tmp/preview.png",
            [CORRECTION_REMOVE_SUN, CORRECTION_ADD_KEY_LIGHT,
             CORRECTION_ADD_FILL_LIGHT, CORRECTION_REFRAME_CAMERA,
             CORRECTION_RERENDER_PREVIEW],
        )
        assert "objects.remove" in script         # neutralisation Sun
        assert 'Key_Light"' in script             # ajout Key_Light
        assert 'Fill_Light"' in script            # ajout Fill_Light
        assert "scene.camera = _cam" in script    # cadrage caméra
        assert "render.render" in script          # re-rendu


# ---------------------------------------------------------------------------
# Imports forbidden — le module ne doit PAS importer blender_client
# ---------------------------------------------------------------------------

def test_module_does_not_import_blender_client():
    """Garde-fou H.4.8 : pas d'import circulaire vers blender_client."""
    import app.engine.blender_runtime_corrector as mod
    source = Path(mod.__file__).read_text(encoding="utf-8")
    assert "from app.clients.blender_client" not in source
    assert "import app.clients.blender_client" not in source
    assert "import blender_client" not in source


# ---------------------------------------------------------------------------
# apply_corrections — orchestration avec subprocess mocké
# ---------------------------------------------------------------------------

class TestApplyCorrections:

    def _make_blend(self, tmp_path: Path) -> str:
        blend = tmp_path / "scene.blend"
        blend.write_bytes(b"FAKE_BLEND")
        return str(blend)

    def test_skipped_when_no_product_subject(self, tmp_path):
        blend = self._make_blend(tmp_path)
        result = apply_corrections(
            exe="blender",
            blend_path=blend,
            output_dir=str(tmp_path),
            render_path=str(tmp_path / "preview.png"),
            template_name="product_render",
            object_names=["Backdrop_Plane", "Camera"],
            initial_violations=[],
            timeout=60,
        )
        assert result["status"] == "skipped"
        assert result["reason"] == "no_product_subject"
        assert result["corrections_applied"] == []

    def test_skipped_when_template_not_product_render(self, tmp_path):
        blend = self._make_blend(tmp_path)
        result = apply_corrections(
            exe="blender",
            blend_path=blend,
            output_dir=str(tmp_path),
            render_path=None,
            template_name="interior_space",
            object_names=["Floor_Plane", "Main_Subject"],
            initial_violations=[],
            timeout=60,
        )
        assert result["status"] == "skipped"
        assert result["reason"] == "template_not_product_render"

    def test_not_available_when_exe_none(self, tmp_path):
        blend = self._make_blend(tmp_path)
        result = apply_corrections(
            exe=None,
            blend_path=blend,
            output_dir=str(tmp_path),
            render_path=str(tmp_path / "preview.png"),
            template_name="product_render",
            object_names=["Product_Subject", "Sun"],
            initial_violations=[],
            timeout=60,
        )
        assert result["status"] == "not_available"

    def test_error_when_blend_missing(self, tmp_path):
        result = apply_corrections(
            exe="blender",
            blend_path=str(tmp_path / "nonexistent.blend"),
            output_dir=str(tmp_path),
            render_path=None,
            template_name="product_render",
            object_names=["Product_Subject", "Sun"],
            initial_violations=[],
            timeout=60,
        )
        assert result["status"] == "error"
        assert result["reason"] == "blend_path_not_found"

    def test_applied_when_subprocess_succeeds(self, tmp_path):
        blend = self._make_blend(tmp_path)
        success_proc = MagicMock()
        success_proc.returncode = 0
        success_proc.stdout = ""
        success_proc.stderr = ""

        with patch(
            "app.engine.blender_runtime_corrector.subprocess.run",
            return_value=success_proc,
        ):
            result = apply_corrections(
                exe="blender",
                blend_path=blend,
                output_dir=str(tmp_path),
                render_path=str(tmp_path / "preview.png"),
                template_name="product_render",
                object_names=["Backdrop_Plane", "Pedestal", "Product_Subject",
                              "Camera", "Sun"],
                initial_violations=[],
                timeout=60,
            )

        assert result["status"] == "applied"
        assert CORRECTION_REMOVE_SUN in result["corrections_applied"]
        assert CORRECTION_ADD_KEY_LIGHT in result["corrections_applied"]
        assert CORRECTION_ADD_FILL_LIGHT in result["corrections_applied"]
        assert CORRECTION_REFRAME_CAMERA in result["corrections_applied"]
        assert CORRECTION_RERENDER_PREVIEW in result["corrections_applied"]

    def test_error_on_subprocess_failure(self, tmp_path):
        blend = self._make_blend(tmp_path)
        failing_proc = MagicMock()
        failing_proc.returncode = 1
        failing_proc.stdout = ""
        failing_proc.stderr = "Some Blender error"

        with patch(
            "app.engine.blender_runtime_corrector.subprocess.run",
            return_value=failing_proc,
        ):
            result = apply_corrections(
                exe="blender",
                blend_path=blend,
                output_dir=str(tmp_path),
                render_path=str(tmp_path / "preview.png"),
                template_name="product_render",
                object_names=["Product_Subject", "Sun"],
                initial_violations=[],
                timeout=60,
            )

        assert result["status"] == "error"
        assert "returncode=1" in result["reason"]
        assert result["corrections_applied"] == []

    def test_error_on_timeout(self, tmp_path):
        blend = self._make_blend(tmp_path)
        with patch(
            "app.engine.blender_runtime_corrector.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="blender", timeout=60),
        ):
            result = apply_corrections(
                exe="blender",
                blend_path=blend,
                output_dir=str(tmp_path),
                render_path=str(tmp_path / "preview.png"),
                template_name="product_render",
                object_names=["Product_Subject", "Sun"],
                initial_violations=[],
                timeout=60,
            )
        assert result["status"] == "error"
        assert result["reason"] == "timeout"

    def test_cleans_up_temp_script(self, tmp_path):
        """Le script de correction temporaire doit être supprimé après exécution."""
        blend = self._make_blend(tmp_path)
        success_proc = MagicMock()
        success_proc.returncode = 0
        success_proc.stdout = ""
        success_proc.stderr = ""

        with patch(
            "app.engine.blender_runtime_corrector.subprocess.run",
            return_value=success_proc,
        ):
            apply_corrections(
                exe="blender",
                blend_path=blend,
                output_dir=str(tmp_path),
                render_path=str(tmp_path / "preview.png"),
                template_name="product_render",
                object_names=["Product_Subject", "Sun"],
                initial_violations=[],
                timeout=60,
            )

        assert not (tmp_path / "_correction_scene.py").exists()
