# Experiment B — the packshot under contract inside a Gaussian-splat world

**Status: done — one image, publishable with its report.** The pipeline's deterministic packshot
(watch, pedestal, contract camera) rendered inside a captured Gaussian-splat environment, with the
framing contract **passed** (occupancy 0.30, band [0.25, 0.55]), the Console's red/green divergence
overlay drawn on the image (IoU 0.99, no divergence), and the splat's provenance — source, sha256,
declared license — recorded in the manifest. Deliverables in [`results/`](results/).

World models and 3D capture produce environments; none of them ship production discipline. AAC's
thesis is that such worlds are **untrusted input, put under contract**. This experiment executes that
sentence once, end to end, at the smallest useful scale: one image, no new pipeline feature.

## What the image proves

- **The contract travels.** `app.engine.framing_contract` is pure (no bpy, no I/O); the exact code
  path the pipeline runs in the container evaluates this out-of-pipeline render unchanged.
- **The overlay is the Console's.** Red = product footprint seen in pixels, green = bbox computed
  from geometry, same colors, same legend, drawn on the shipped image instead of client-side SVG.
- **Provenance is part of the artifact.** `results/manifest.json` mirrors the core manifest shape and
  adds a `provenance` block: source run id, splat file + sha256 + declared license + rights caveat,
  addon name + pinned version, the exact splat transform used.

## Method

1. **Product** — appended from a real pipeline run's `scene.blend` (`Pedestal`, `Product_Subject`,
   `Camera`). The run's camera is reused as-is: the pipeline saves `scene.blend` *after* its
   hero-framing corrector, so that camera is the contract-passing one (occupancy lands exactly on
   its 0.30 target).
2. **World** — `playroom` (1.9M splats, Deep Blending capture, 3DGS reconstruction, downloaded from
   the Voxel51 Hugging Face mirror) imported with the KIRI *3DGS Render* addon, pinned release,
   in the host Blender 5.1.1. The environment is rotated/positioned around the product via
   [`layout.json`](layout.json); product, camera and lights never move.
3. **Render** — EEVEE, 1920×1080 (the pipeline's canonical aspect — see finding below), composite
   pass + a product-only `film_transparent` mask pass, plus a dump of the camera parameters and the
   subject's 8 world-space bbox corners (`framing_raw.json`).
4. **Contract** — [`contract_and_overlay.py`](contract_and_overlay.py) computes the geometric
   invariants with the core module, takes the perceptual bbox from the mask's alpha, computes the
   projected-vs-perceptual divergence, writes `contract_report.json` + `manifest.json`, and draws
   `hero_overlay.png`.

## Findings (kept)

- **The occupancy band is calibrated for the canonical 16:9 frame.** The same camera and subject
  read occupancy 0.30 at 1920×1080 but ~0.17 in a square render (sensor-fit changes the frame's
  half-height): a square export false-fails the contract. Worth a note in the contract docs.
- **The saved `scene.blend` camera is post-correction.** Rebuilding the "canonical" camera from the
  template constants reproduces the *pre*-hero-framing view (occupancy 0.23, out of band). Anything
  that reuses a run's scene must take the saved camera, not the constants.
- **The pixel visual-QA assumes a studio backdrop.** On a photographic splat background its
  band-scan heuristic has no meaning, so the perceptual bbox here comes from an object mask pass
  instead. A detector that survives arbitrary backgrounds (object-index / cryptomatte pass) is a
  roadmap item.

## Honest limits

- **Lighting is matched by hand.** The splat's light is baked into the capture; the product is lit
  by two area lamps tuned by eye to sit in it. No automatic harmonization — this is *the* hard
  problem, and it is out of scope here.
- **The red box measures footprint, not visibility.** Both boxes describe the same isolated subject
  (mask pass), so occlusion by the environment would not register as divergence.
- **License chain recorded, not re-audited.** The Hugging Face redistributor declares Apache-2.0;
  the underlying capture is from the Deep Blending research dataset. Both facts are in
  [`assets.json`](assets.json) and in the manifest.

## Reproduce

Host-side (the hardened backend container has no addon and stays that way by design):

```bash
cd experiments/splat_demo

# one-time: pinned addon + splat downloads, sha256-checked (see assets.json)
# addon installed under ~/.config/blender/5.1/scripts/addons/kiri_3dgs_render
# playroom_30k.ply imported once into assets/playroom_import.blend (~10 min for 1.9M splats)

blender --background assets/playroom_import.blend --python assemble_scene.py -- \
  --layout layout.json --out results          # add --preview for fast half-res iteration

../jepa_eval/.venv/bin/python contract_and_overlay.py --dir results   # needs pillow only
```

Import pitfall, for the record: fresh KIRI imports arrive with `Update Mode = 1` (frozen view
matrix) and render blank in headless mode. `assemble_scene.py` sets the socket to 0 and refreshes
the splat object against the scene camera before rendering.
