# Experiment A — a V-JEPA "learned eye" next to the deterministic contracts

**Status: done, hardened — real signal.** Primary AUC **0.975** over 320 within-case pairs
(pre-registered threshold for "real signal": ≥ 0.80), across 4 defect families × 4 intensities.
V-JEPA is the only tested representation that never drops below 0.80 at any intensity; the trivial
baselines (raw pixels, color histograms — same protocol) **invert below chance** on the subtle
structural defect (small intruder cube, AUC down to 0.00), the very defect the deployed contract
cannot see either. Full numbers in [`results/`](results/), analysis in [`BENCHMARK.md §4`](../../BENCHMARK.md).

AAC verifies product renders with deterministic contracts (required objects, visual QA, geometric framing).
This experiment adds a third look of a different nature: a **learned metric**. A frozen video world-model encoder
([V-JEPA 2.1](https://github.com/facebookresearch/vjepa2), ViT-L/16, MIT license) embeds each render; the score is
the cosine similarity to the centroid of conforming renders of the same case. The metric sits **next to** the
contracts — never instead of them.

## Success criteria — decided before running

The question: does the learned score separate conforming renders from deliberately degraded ones?
Measured by AUC (probability that a conforming render scores above a degraded one; 0.5 = chance).

| AUC | Verdict |
|---|---|
| ≥ 0.80 | real signal — keep the metric, document it |
| 0.60 – 0.80 | weak — document, investigate (crops, multi-view) before concluding |
| ≤ 0.60 | negative — documented as-is: "a generalist video embedding is not enough to judge a product render" is a publishable lesson |

*Stretch goal:* the JEPA score catches ≥ 1 defect the deterministic contract cannot see (e.g. lighting incoherence).

## Method

1. **Labeled dataset** — the prompts of the product-render eval corpus (`core/app/engine/product_render_eval_cases.py`)
   rendered through the real pipeline, then mutated: 4 conforming variants (pipeline output + 3 in-contract camera
   jitters) and 4 defect families **at 4 intensities each** (key light dimmed 25/50/75 % then removed · framing slightly
   off → broken · intruder cube 4 → 15 cm · colored rim light 25 → 200 W). 512×512, EEVEE. Every variant records what
   the deployed contract sees (`contract_verdict`). **5 of the 11 corpus cases qualify** — the other 6 route to the
   legacy scaffold path where no contract applies (upstream template routing; recorded in the dataset's
   `excluded.json`). Final dataset: **100 images**.
2. **Encode** — each image through the frozen encoder (no training, no fine-tuning).
3. **Score** — cosine similarity to the leave-one-out centroid of the same case's conforming embeddings.
4. **Separate** — global AUC + per-defect AUC (pure-Python Mann-Whitney; no sklearn).
5. **Report** — `report.json` + summary; results feed `BENCHMARK.md`.

## Honest scope

JEPA embeddings capture motion, physics and semantics — **not beauty**. The corpus is small (11 cases), so no fine
calibration. Single still images only (video/turntable is V-JEPA's native ground — later, if the signal warrants it).
This is not a quality oracle; it is a third look.

## Reproduce

Host-side (the hardened backend container stays torch-free by design):

```bash
cd experiments/jepa_eval
python3 -m venv .venv && source .venv/bin/activate
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
pip install -r requirements.txt

python make_dataset.py                       # drives the running AAC stack (docker compose up first)
python encode_and_score.py                   # V-JEPA (GPU for comfort; CPU works, slower)
python encode_and_score.py --embedder pixel      # trivial baseline: raw 64x64 pixels
python encode_and_score.py --embedder histogram  # trivial baseline: RGB color histograms
```

Dataset and reports land in `docker/outputs/blender/_jepa_eval/` (host side, gitignored).
