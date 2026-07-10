# Benchmark — LLM quality baseline

**What this measures.** AAC's Blender pipeline relies on a local LLM at two points: it **extracts a structured scene plan** from a natural-language request, then **writes the `bpy` script** that builds the scene. This document reports a measured, reproducible baseline for *both* of those LLM sites — not a vibe check, a number.

> **Honest scope.** This benchmark covers **only the two LLM sites of the Blender pipeline**. The router/classifier, the web-search path and the ComfyUI image pipeline are **not yet measured** (planned — see [Roadmap](#roadmap)). The corpora are small (5 and 11 cases): treat these as a **baseline harness**, not an exhaustive evaluation. The value here is the *method* — versioned corpus, pinned inference, deterministic scoring — as much as the score.

- **Default model:** `qwen2.5-coder:7b` (local, via Ollama); cross-model comparison covers 6 candidates (3 B–16 B).
- **Date:** 2026-07-01 (extraction-prompt fix: `schema_version` 0.545 → 1.000 on the default model)
- **Hardware:** single RTX 3060 (12 GB)

---

## Method

- **Versioned corpus.** Each evaluation runs against a fixed, named set of cases, so two runs are comparable. Case IDs are recorded in every report.
- **Pinned inference.** Generation is deterministic: `temperature=0.0`, `top_k=1`, `top_p=1.0`, `seed=42`, `num_ctx=8192`. This makes a run **reproducible** — same config, same output.
- **Deterministic scoring.** Each case is scored against an explicit checklist; identical model output yields a bit-identical score. No floating thresholds, no hidden randomness.
- **Reproducibility ≠ robustness.** Because the seed is fixed, the multi-run pass (below) measures **reproducibility** (does the same input give the same score?), *not* robustness to sampling noise. True cross-seed robustness — varying the seed — is **not yet wired in** and is left honest as future work.

---

## Results

### 1. `script_gen` — quality of the generated Blender script

5 cases · **3 runs** (reproducibility check).

| Metric | Result |
| --- | --- |
| Mean score | **0.967 / 1.0** |
| Generation succeeded | **100 %** |
| Valid Python (AST-parseable) | **100 %** |
| Correct scene template selected | **100 %** |
| Reproducibility (std-dev across 3 runs) | **0.000** |
| Mean time per case | ~10.1 s |

Per case: `freeform_metal_sphere_floating`, `freeform_low_poly_tree`, `ambiguous_atelier_artiste` → **1.000**; the two interior scenes (`interior_salon_moderne`, `interior_cuisine_industrielle`) → **0.917** — the model is slightly weaker on richer interiors.

### 2. `product_render` — quality of the structured scene extraction

11 cases · measured three times on 2026-07-01 (single run, 5-seed consolidation, cross-model session).

| Metric | Result |
| --- | --- |
| Parse OK | **100 %** (every session) |
| Mean score | **0.98 – 1.00** (1.000 · 0.982 · 0.987 across the three sessions) |

Per-field accuracy:

| Field | Accuracy |
| --- | --- |
| `framing`, `subject.*` (shape, color, material, kind, cap, transparency) | **1.000** |
| `subject.kind_fidelity`, `backdrop.color`, `pedestal.*` | 1.000 |
| **`schema_version`** | **1.000** *(was 0.545 — fixed this revision, see below)* |

> **The benchmark drove a fix — again.** The 2026-06-30 revision fixed `backdrop.color`/`pedestal.color` at the prompt level. This revision closes the last systemic gap: `schema_version` (0.545 → 1.000). Root cause: the model *inferred* V1 attributes from the object type ("tube" → cylindrical) and then labeled the extraction v1 even when the request never stated them. The fix is an **anti-inference rule** in the extraction prompt — a V1 field may only be filled when the request states it explicitly — plus two contrastive examples. No model change, no scoring change, one prompt edit.

**Honest nuance — reproducibility has a boundary.** Within one server session, greedy decoding is bit-identical (5 seeds, stdev 0.000). *Across* sessions, exactly one borderline case can flip (observed twice: the amber-bottle v0/v1 label; the watch pedestal color) — hence the 0.98–1.00 range instead of a single number. Reproducibility holds within a session; the residual across-session variance is ±1 case (±0.018 on the mean).

### 3. Cross-model comparison — `product_render`

The whole point of a benchmark is to **compare and decide**. Six locally-runnable models, same 11-case corpus, seed `42`, on the RTX 3060 (12 GB). `ollama` loads one model at a time, so VRAM only ever holds the largest single model.

| Rank | Model | Mean score | Parse OK | Time (11 cases) | Note |
| --- | --- | --- | --- | --- | --- |
| 1 | **`qwen2.5-coder:7b`** *(default)* | **0.987** | 100 % | **34.4 s** | best quality outright, and fastest of the strong models |
| 2 | `qwen2.5-coder:14b` | 0.951 | 100 % | 56.9 s | clearly behind the 7 B now, for 1.65× the time |
| 3 | `qwen2.5:7b` *(generalist)* | 0.931 | 100 % | 40.0 s | biggest beneficiary of the clearer prompt (was 0.809) |
| 4 | `qwen2.5-coder:3b` | 0.862 | 100 % | 29.2 s | smallest, fastest |
| 5 | `codegemma:7b` | 0.828 | 90.9 % | 67.3 s | parse regressed under the longer prompt |
| 6 | `deepseek-coder-v2:16b` | 0.822 | 90.9 % | 48.9 s | was 3rd (0.900) on the previous prompt — dropped, with parse failures |

**What it shows:**

- **The default is the right call — now outright.** `qwen2.5-coder:7b` is **first, alone** (0.987) *and* the fastest of the strong models — 1.65× faster than the runner-up. On the previous prompt it tied the 14 B; on the tightened prompt it simply wins. The model choice is *evidence-backed*, not assumed.
- **A spec fix beat a model swap.** `schema_version` was weak for *every* model on the old prompt (best 0.636) — a spec problem, not a model problem. One prompt edit later, the default scores 1.000 on it. No amount of "try a bigger model" would have found that; the per-field breakdown did.
- **A benchmark measures the pair, not the model.** Same corpus, same scoring, new prompt: `deepseek-coder-v2:16b` fell from 3rd (0.900) to 6th (0.822) with parse failures, while the generalist `qwen2.5:7b` jumped +0.122. Rankings are **model × prompt fit** — which is exactly why the comparison is re-run after every prompt change instead of quoting stale numbers.
- **Code fine-tuning still earns its keep — less starkly.** `coder:7b` (0.987) beats the *same-base, same-size* generalist (0.931) by +0.056 (was +0.096). The clearer the spec, the smaller the specialization gap — an expected and honest convergence.
- **Returns still flatten past 7 B.** The 14 B scores *below* the 7 B (0.951 vs 0.987) for 1.65× the latency. At this scale, paying for more parameters buys no quality.

> **Consolidation (5 seeds).** Re-running the default (`coder:7b`) across seeds `42,7,1,123,999` returns **bit-identical scores within the session** (mean 0.982 every time, stdev 0.000). Note *why*: the canonical config is greedy (`temperature=0`, `top_k=1`), so the seed does not change the output. This proves **reproducibility**, not robustness to sampling noise — measuring the latter would require `temperature > 0`, and is left as honest future work. Across sessions, one borderline case can flip (see the nuance in section 2). The corpus is also small (11 cases), so per-field figures stay indicative rather than definitive.

### 4. `product_render` — a learned metric next to the contracts (experimental)

Deterministic contracts tell you *which rule broke* — but they can't see what they were never taught. This experiment puts a frozen world-model encoder next to them ([V-JEPA 2.1](https://github.com/facebookresearch/vjepa2) ViT-L/16, MIT, pinned commit) and asks one question: does an embedding distance separate conforming renders from degraded ones? Protocol, dataset builder and success thresholds were fixed **before** the first measurement — see [`experiments/jepa_eval/`](experiments/jepa_eval/).

**Setup.** 5 corpus cases rendered by the real pipeline, then mutated: 4 conforming variants per case (pipeline output + 3 in-contract camera jitters) and 4 defect families **at 4 intensities each** — key light dimmed 25/50/75 % then removed · framing from slightly off to broken · an intruder cube from 4 cm to 15 cm · a colored rim light from 25 W to 200 W. 100 images, labeled by construction, each recording what the deployed contract sees. Score = cosine similarity to the case's conform centroid, leave-one-out. Two **trivial baselines** run the exact same protocol with dumb representations (raw 64×64 pixels; RGB color histograms) — the control that says whether the world model earns anything.

AUC per defect × intensity (1 = weakest), 20 within-case pairs each:

| Defect (weak → strong) | V-JEPA | pixels | histogram | Contract sees it? |
|---|---|---|---|---|
| key light dimmed 25 % → removed | **0.80 · 1.0 · 1.0 · 1.0** | 0.25 · 0.50 · 1.0 · 1.0 | 1.0 · 1.0 · 1.0 · 1.0 | only full removal |
| framing slightly off → broken | **1.0 · 1.0 · 1.0 · 1.0** | 0.95 · 1.0 · 1.0 · 1.0 | 0.45 · 0.95 · 1.0 · 1.0 | partially (signal-only pixel checks) |
| intruder cube 4 → 15 cm | **0.85 · 0.95 · 1.0 · 1.0** | 0.25 · 0.25 · 0.40 · 0.60 | 0.00 · 0.15 · 0.55 · 0.80 | **no** — only `Sun` is forbidden |
| colored rim light 25 → 200 W | **1.0 · 1.0 · 1.0 · 1.0** | 1.0 · 1.0 · 1.0 · 1.0 | 1.0 · 1.0 · 1.0 · 1.0 | **no** — lighting coherence unchecked |

**Result: primary AUC 0.975 over 320 within-case pairs** (pre-registered thresholds: ≥ 0.80 = real signal). Three readings:

- **V-JEPA is the only representation that never drops below 0.80 at any defect intensity**, and it bends exactly where a human would hesitate: a 4 cm cube (0.85), a 25 % light dim (0.80).
- **The baselines don't just fail on subtle structural defects — they invert.** A small intruder cube scores *below chance* on pixels and histograms (down to AUC 0.00): legitimate camera jitter moves more pixels than an illegitimate small cube, so the defect looks *more normal* than the conforming variants. Trivial metrics measure **change**; the embedding measures something closer to **conformity** — which is the whole point.
- **The defect that is both contract-blind and baseline-resistant — the intruder object — is exactly where the world model earns its keep.**

**Honest scope.** Tiny corpus (5 cases), synthetic defects, single still images, frozen encoder: this is *a third look, not a quality oracle* — it says nothing about beauty. Next hard test: video/turntable input (V-JEPA's native ground) and defects below these intensities.

**A finding made on the way: 6 of the 11 corpus prompts never reached the deterministic builder.** The upstream template routing sent them down the legacy scaffold path, where no contract applies (recorded per case in the dataset's `excluded.json`). The extraction corpus was built to test the *extractor*; the template routing in front of it turned out to be untested. **Closed (2026-07-10):** bare object-on-a-backdrop prompts ("boîte rouge brillante sur fond noir") now route to the builder via a composed signal — an object noun *and* a backdrop marker, never one without the other — and the whole corpus is locked by a routing harness (`core/tests/test_template_routing_eval_corpus.py`, negatives included).

---

## Reproduce it yourself

The stack must be up (`./run.sh`). Both runners execute **inside** the hardened backend container.

```bash
# Generated-script quality, 3 runs (reproducibility)
docker exec aac-aac-backend-1 \
  python -m app.engine.script_gen_eval_runner --runs 3 \
  --base-dir /outputs/blender/_eval_reports

# Scene-extraction quality
docker exec aac-aac-backend-1 \
  python -m app.engine.product_render_eval_runner \
  --base-dir /outputs/blender/_eval_reports

# Cross-model comparison (models must be pulled first; absent ones are skipped)
docker exec aac-aac-backend-1 \
  python -m app.engine.product_render_eval_runner \
  --models 'qwen2.5-coder:3b,qwen2.5-coder:7b,qwen2.5:7b,codegemma:7b,qwen2.5-coder:14b,deepseek-coder-v2:16b' \
  --base-dir /outputs/blender/_eval_reports

# Cross-seed robustness (consolidates a single model over seeds 42,7,1,123,999)
docker exec aac-aac-backend-1 \
  python -m app.engine.product_render_eval_runner --multi-seed \
  --base-dir /outputs/blender/_eval_reports
```

> **Why `--base-dir /outputs/...`?** The container runs with a **read-only root filesystem** (the hardening boundary — see [`SECURITY.md`](SECURITY.md)). The only writable path is the `/outputs` volume. A default relative path fails with `Read-only file system` — by design. Reports land on the host under `docker/outputs/blender/_eval_reports/`.

Each run writes a timestamped JSON report (lexicographic order = chronological order), and `script_gen` also persists every extracted script for human inspection.

The learned-metric experiment (section 4) reproduces from [`experiments/jepa_eval/`](experiments/jepa_eval/) — dataset builder, encoder loading (pinned + reviewed) and scoring are documented in its README; its report lands in `experiments/jepa_eval/results/`.

---

## Roadmap

- **Grow the corpora** beyond 5 / 11 cases.
- **Cross-seed robustness**: vary the seed to measure sampling noise, not just reproducibility.
- **Extend coverage** to the router/classifier, the web-search path and the ComfyUI image pipeline (new harnesses). *(Template routing: done — found by the learned-metric experiment, closed by the corpus routing harness, 2026-07-10.)*
- **Harden the learned metric** (section 4): threshold-of-visibility defects, turntable (video) input, and a `jepa_score` column in the eval reports if the signal keeps earning it.
- Track baselines **across models** to compare candidates objectively.
