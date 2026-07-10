# Host script — NO bpy. Run with the jepa_eval venv (pillow):
#   ../jepa_eval/.venv/bin/python contract_and_overlay.py --dir <render dir>
#
# Puts an assembled splat render under the framing contract:
#   framing_raw.json (camera + subject corners, dumped by assemble_scene.py)
#   mask.png         (product-only alpha pass) -> perceptual bbox
# and produces:
#   contract_report.json  — framing_contract block, same shape as scene_report's
#   hero_overlay.png      — render.png + the Console's red/green framing overlay
#   manifest.json         — core manifest shape + provenance (splat, source run)
#
# Imports app.engine.framing_contract from core/ (pure module: no bpy, no I/O).
# Do NOT import the vendored vjepa2 tree in the same process (it also ships a
# top-level `app` package).

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "core"))

from app.engine import framing_contract as fc  # noqa: E402

RED = "#e5484d"    # perceptual — seen by the camera (pixels)
GREEN = "#30a46c"  # projected — computed (geometry)
ALPHA_THRESHOLD = 16  # mask alpha above this counts as product footprint


def perceptual_bbox_from_mask(mask_path: Path):
    """Alpha bbox of the product-only pass, as [left, top, right, bottom] fractions."""
    img = Image.open(mask_path).convert("RGBA")
    alpha = img.getchannel("A").point(lambda a: 255 if a > ALPHA_THRESHOLD else 0)
    box = alpha.getbbox()
    if box is None:
        return None
    l, t, r, b = box
    return [l / img.width, t / img.height, r / img.width, b / img.height]


def draw_overlay(render_path: Path, out_path: Path, perceptual, projected, iou, diverged):
    img = Image.open(render_path).convert("RGB")
    draw = ImageDraw.Draw(img, "RGBA")
    w, h = img.size
    stroke = max(2, round(min(w, h) / 270))

    for frac_box, color in ((perceptual, RED), (projected, GREEN)):
        if frac_box:
            px = [frac_box[0] * w, frac_box[1] * h, frac_box[2] * w, frac_box[3] * h]
            draw.rectangle(px, outline=color, width=stroke)

    font = ImageFont.load_default(size=max(14, h // 40))
    pad, sw = h // 90 + 6, h // 45
    parts = [(RED, "seen by the camera (pixels)"), (GREEN, "computed (geometry)")]
    tail = f"· IoU {iou:.2f} · diverged: {'yes' if diverged else 'no'}" if iou is not None else ""
    text_h = font.getbbox("Ag")[3]
    strip_h = text_h + 2 * pad
    draw.rectangle([0, h - strip_h, w, h], fill=(13, 13, 13, 185))
    x, y = pad, h - strip_h + pad
    for color, label in parts:
        draw.rectangle([x, y + text_h * 0.25, x + sw, y + text_h * 0.85], fill=color)
        x += sw + pad
        draw.text((x, y), label, font=font, fill="#ededed")
        x += draw.textlength(label, font=font) + 2 * pad
    if tail:
        draw.text((x, y), tail, font=font, fill="#ededed")
    img.save(out_path)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True, help="dir with render.png, mask.png, framing_raw.json")
    ap.add_argument("--layout", default=str(Path(__file__).parent / "layout.json"))
    ap.add_argument("--assets", default=str(Path(__file__).parent / "assets.json"))
    args = ap.parse_args()

    out = Path(args.dir)
    layout = json.loads(Path(args.layout).read_text(encoding="utf-8"))
    assets = json.loads(Path(args.assets).read_text(encoding="utf-8"))
    raw = json.loads((out / "framing_raw.json").read_text(encoding="utf-8"))

    # 1. Geometric framing invariants (same pure code path as the pipeline).
    half_w, half_h = fc.half_extents_at_unit_depth(
        raw["lens"], raw["sensor_width"], raw["sensor_height"], raw["sensor_fit"],
        raw["res_x"], raw["res_y"], raw["pixel_aspect_x"], raw["pixel_aspect_y"],
    )
    framing = fc.evaluate_framing(
        raw["view_matrix"], {"half_w": half_w, "half_h": half_h}, raw["subject_corners"]
    )

    # 2. Perceptual bbox from the product-only mask pass (not visual_qa: that
    #    detector assumes a studio backdrop — documented limit of this demo).
    perceptual = perceptual_bbox_from_mask(out / "mask.png")
    divergence = fc.framing_divergence(framing.get("screen_bbox"), perceptual)
    framing["framing_divergence"] = divergence

    report = {
        "status": framing["status"],
        "violations": framing["violations"],
        "gates": ["framing_contract"],
        "framing_contract": framing,
    }
    (out / "contract_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    # 3. Overlay, Console colors and legend.
    projected = (
        fc.screen_bbox_to_top_left_fraction(framing["screen_bbox"])
        if framing.get("screen_bbox") else None
    )
    draw_overlay(
        out / "render.png", out / "hero_overlay.png",
        perceptual, projected, divergence.get("iou"), divergence.get("diverged"),
    )

    # 4. Manifest: core top-level shape + provenance of every untrusted input.
    run_blend = Path(layout["source_run_blend"])
    run_manifest_path = run_blend.parent / "manifest.json"
    run_manifest = (
        json.loads(run_manifest_path.read_text(encoding="utf-8"))
        if run_manifest_path.exists() else {}
    )
    splat_file = Path(layout["splat_blend"])
    splat_entry = next(
        (s for s in assets["splats"] if Path(s["file"]).stem in splat_file.stem
         or Path(s["file"]).stem == layout["splat_object"]),
        None,
    )
    artifacts = {}
    for key, name in (
        ("render_png", "render.png"), ("mask_png", "mask.png"),
        ("hero_overlay_png", "hero_overlay.png"),
        ("contract_report", "contract_report.json"),
        ("framing_raw", "framing_raw.json"), ("manifest", "manifest.json"),
    ):
        artifacts[key] = {"path": str(out / name), "exists": (out / name).exists() or key == "manifest"}

    manifest = {
        "manifest_version": run_manifest.get("manifest_version", 1),
        "pipeline": "splat_demo_experiment",
        "request_id": run_manifest.get("request_id"),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": report["status"],
        "output_dir": str(out),
        "input": {
            "prompt": run_manifest.get("input", {}).get("prompt"),
            "task_type": "experiment_splat_demo",
        },
        "artifacts": artifacts,
        "scene_report": {"status": report["status"], "violations": report["violations"]},
        "execution": {"blender_status": "success", "blender_error": None},
        "provenance": {
            "source_run": {
                "run_id": run_blend.parent.name,
                "scene_blend": str(run_blend),
                "camera": "hero-framing corrected camera reused as-is (contract-passing)",
            },
            "splat": {
                **({k: splat_entry[k] for k in (
                    "scene", "file", "sha256", "source_url",
                    "license_declared", "license_caveat",
                )} if splat_entry else {"error": "splat not found in assets.json"}),
                "addon": assets["addon"]["name"],
                "addon_version": assets["addon"]["version"],
                "transform": layout["splat_transform"],
            },
        },
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print("CONTRACT:", report["status"], report["violations"],
          "| occupancy", framing.get("occupancy"),
          "| IoU", divergence.get("iou"), "| diverged", divergence.get("diverged"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
