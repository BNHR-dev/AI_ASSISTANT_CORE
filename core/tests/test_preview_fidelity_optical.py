"""
H.6.11 — preuve optique CONTRÔLÉE et DÉTERMINISTE de la fidélité matière, dans
le chemin réellement livré (runtime corrector).

Principe : un motif damier fortement contrasté et émissif est placé PHYSIQUEMENT
derrière un sujet en verre. Le rendu passe par `apply_corrections` (la fonction
de production : plan → script avec bloc fidélité partagé → subprocess Blender).

Critère verre  : le motif est visible À TRAVERS le verre — l'énergie de contour
                 dans la silhouette du sujet est très supérieure à celle d'un
                 sujet opaque identique (qui occulte le motif).
Critère métal  : avec un environnement clair/sombre structuré, le sujet chromé
                 montre une réflexion lisible (énergie de contour >> opaque mat).

La déformation par réfraction (IOR 1.45) est à confirmer visuellement sur les
artefacts émis sous outputs/blender/h611_optical/.

Le test est SKIPPÉ si Blender n'est pas disponible (intégration locale, pas CI).
N'altère pas l'environnement livré : le contraste vient de la FIXTURE.
"""
import statistics
import subprocess
from pathlib import Path

import pytest

from app.clients.blender_client import resolve_blender_exe
from app.engine.blender_runtime_corrector import apply_corrections

PIL = pytest.importorskip("PIL")
from PIL import Image, ImageChops, ImageStat  # noqa: E402

_EXE = resolve_blender_exe()
pytestmark = pytest.mark.skipif(_EXE is None, reason="Blender introuvable")

_FIXTURE_BUILDER = Path(__file__).parent / "fixtures" / "preview_fidelity_scene.py"
_OBJECT_NAMES = [
    "Backdrop_Plane", "Pedestal", "Product_Subject",
    "Camera", "Key_Light", "Fill_Light",
]
# Dossier inspectable (artefacts de validation, non versionnés).
_ARTIFACT_DIR = Path("outputs/blender/h611_optical")


def _build_fixture(mode: str, blend_path: Path) -> None:
    proc = subprocess.run(
        [_EXE, "--background", "--factory-startup", "--python",
         str(_FIXTURE_BUILDER), "--", mode, str(blend_path)],
        capture_output=True, text=True, timeout=120,
    )
    assert blend_path.exists(), f"fixture {mode} non construite:\n{proc.stdout}\n{proc.stderr}"


def _render_via_corrector(blend_path: Path, render_path: Path) -> None:
    """Rend la preview via le chemin de production exact (runtime corrector)."""
    res = apply_corrections(
        _EXE, str(blend_path), str(blend_path.parent), str(render_path),
        template_name="product_render", object_names=_OBJECT_NAMES,
        initial_violations=[], timeout=120,
    )
    assert res["status"] == "applied", f"corrector non appliqué: {res}"
    assert "rerender_preview" in res["corrections_applied"]
    assert render_path.exists(), "preview non produite par le corrector"


def _gray(path: Path) -> Image.Image:
    return Image.open(path).convert("L")


def _subject_mask(test_gray: Image.Image, opaque_gray: Image.Image) -> Image.Image:
    """Région où le sujet diffère du sujet opaque = sa silhouette."""
    diff = ImageChops.difference(test_gray, opaque_gray)
    return diff.point(lambda p: 255 if p > 20 else 0)


def _luminance_std_in_mask(gray: Image.Image, mask: Image.Image) -> tuple[float, float]:
    """Écart-type de luminance sur les seuls pixels du sujet (mask=255).
    Un matériau qui révèle un motif noir/blanc (verre traversé, métal réfléchi)
    a un std élevé ; un mat lisse a un std faible. Retourne (aire_px, std)."""
    gpx = gray.tobytes()
    mpx = mask.tobytes()
    vals = [gpx[i] for i in range(len(gpx)) if mpx[i] == 255]
    if not vals:
        return 0.0, 0.0
    return float(len(vals)), statistics.pstdev(vals)


@pytest.fixture(scope="module")
def renders(tmp_path_factory):
    d = tmp_path_factory.mktemp("h611_optical")
    _ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    out = {}
    for mode in ("glass", "opaque", "metal"):
        blend = d / f"{mode}.blend"
        png = d / f"{mode}.png"
        _build_fixture(mode, blend)
        _render_via_corrector(blend, png)
        # copie inspectable
        Image.open(png).save(_ARTIFACT_DIR / f"{mode}.png")
        out[mode] = png
    return out


def test_glass_pattern_visible_through_subject(renders):
    """Le motif contrasté derrière le verre est visible à travers lui :
    le std de luminance dans la silhouette est nettement supérieur à celui d'un
    sujet opaque identique (qui occulte le motif)."""
    glass = _gray(renders["glass"])
    opaque = _gray(renders["opaque"])
    mask = _subject_mask(glass, opaque)
    area, std_glass = _luminance_std_in_mask(glass, mask)
    assert area > 2000, f"silhouette sujet trop petite ({area:.0f} px) — fixture douteuse"
    _, std_opaque = _luminance_std_in_mask(opaque, mask)
    assert std_glass > std_opaque * 1.8, (
        f"motif non visible à travers le verre : "
        f"std verre={std_glass:.1f} vs opaque={std_opaque:.1f}"
    )


def test_metal_reflection_visible(renders):
    """Le sujet chromé révèle l'environnement structuré : std de luminance dans
    la silhouette nettement supérieur à celui d'un sujet opaque mat lisse."""
    metal = _gray(renders["metal"])
    opaque = _gray(renders["opaque"])
    mask = _subject_mask(metal, opaque)
    area, std_metal = _luminance_std_in_mask(metal, mask)
    assert area > 2000, f"silhouette sujet trop petite ({area:.0f} px)"
    _, std_opaque = _luminance_std_in_mask(opaque, mask)
    # La réflexion métallique est un signal plus SUBTIL que la réfraction du
    # verre (zones réfléchies plus larges, moins haute-fréquence). Mesure
    # empirique stable (EEVEE déterministe) ~1.49x ; seuil à 1.35 = marge de
    # sécurité tout en exigeant un std nettement > au mat lisse.
    assert std_metal > std_opaque * 1.35, (
        f"réflexion non lisible sur le métal : "
        f"std metal={std_metal:.1f} vs opaque={std_opaque:.1f}"
    )
