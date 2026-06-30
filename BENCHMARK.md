# Benchmark — LLM quality baseline

**What this measures.** AAC's Blender pipeline relies on a local LLM at two points: it **extracts a structured scene plan** from a natural-language request, then **writes the `bpy` script** that builds the scene. This document reports a measured, reproducible baseline for *both* of those LLM sites — not a vibe check, a number.

> **Honest scope.** This benchmark covers **only the two LLM sites of the Blender pipeline**. The router/classifier, the web-search path and the ComfyUI image pipeline are **not yet measured** (planned — see [Roadmap](#roadmap)). The corpora are small (5 and 11 cases): treat these as a **baseline harness**, not an exhaustive evaluation. The value here is the *method* — versioned corpus, pinned inference, deterministic scoring — as much as the score.

- **Default model:** `qwen2.5-coder:7b` (local, via Ollama); cross-model comparison covers 6 candidates (3 B–16 B).
- **Date:** 2026-06-30 (scene-extraction prompt fix: `backdrop`/`pedestal` → 1.000)
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

11 cases · single run.

| Metric | Result |
| --- | --- |
| Parse OK | **100 %** |
| Mean score | **0.905 / 1.0** |

Per-field accuracy surfaces exactly where it breaks down:

| Field | Accuracy |
| --- | --- |
| `framing`, `subject.*` (shape, color, material, kind, cap, transparency) | **1.000** |
| `subject.kind_fidelity` | 1.000 |
| `backdrop.color`, `pedestal.color`, `pedestal.material` | 1.000 |
| **`schema_version`** | **0.545** |

One real weakness remains, stated plainly: the default model is unreliable on `schema_version` (0.545). It is tracked, not hidden — and it is the *next* extraction-prompt fix to make (see the cross-model finding below).

> **The benchmark drove a fix.** The prior revision flagged two extraction gaps — `backdrop.color` (0.900) and `pedestal.color` (0.000 on the single-seed run). A scene-extraction prompt fix (2026-06-30) closed both to **1.000** while resolving a real rendering bug (a wrong backdrop color bleeding a colored cast over neutral packshots). `schema_version` is the remaining systemic gap.

### 3. Cross-model comparison — `product_render`

The whole point of a benchmark is to **compare and decide**. Six locally-runnable models, same 11-case corpus, seed `42`, on the RTX 3060 (12 GB). `ollama` loads one model at a time, so VRAM only ever holds the largest single model.

| Rank | Model | Mean score | Parse OK | Time (11 cases) | Note |
| --- | --- | --- | --- | --- | --- |
| 1 | **`qwen2.5-coder:7b`** *(default)* | **0.905** | 100 % | **44.1 s** | best quality *and* quality-per-second |
| 2 | `qwen2.5-coder:14b` | **0.905** | 100 % | 62.8 s | ties the 7 B, for 1.4× the time |
| 3 | `deepseek-coder-v2:16b` | 0.900 | 100 % | 46.7 s | MoE (~2.4 B active): nearly top, faster than the 14 B *dense* |
| 4 | `qwen2.5:7b` *(generalist)* | 0.809 | 90.9 % | 43.0 s | same base as `coder:7b`, no code tuning |
| 5 | `codegemma:7b` | 0.806 | 100 % | 71.7 s | other-vendor code model |
| 6 | `qwen2.5-coder:3b` | 0.785 | 90.9 % | 30.9 s | smallest, fastest |

**What it shows:**

- **The default is the right call — now unambiguously.** `qwen2.5-coder:7b` is **tied for the best quality** (0.905, with the 14 B) while running **1.4× faster**. It is the best quality *and* the best quality-per-second among the strong models. The model choice is *evidence-backed*, not assumed.
- **Returns flatten, they don't just diminish.** 3b → 7b → 14b reads 0.785 → 0.905 → **0.905**: the 14 B adds **nothing** over the 7 B on this corpus, for 1.4× the latency. At this scale, paying for more parameters buys no quality.
- **Code fine-tuning earns its keep.** `coder:7b` (0.905, 100 % parse) beats the *same-base, same-size* generalist `qwen2.5:7b` (0.809, 90.9 % parse) by **+0.096**. A controlled result: the only variable is the code tuning.
- **Total params ≠ quality.** The 16 B MoE lands 3rd (0.900) — close, and faster than the 14 B dense (confirming the MoE expectation), but not better than the 7 B. Bigger headline number, not better output.
- **A systemic weakness, not a model one.** `schema_version` is weak for *every* model (best 0.636). When all candidates fail at the same field, the cause is the prompt/spec, not the model — exactly the kind of fix just applied to `backdrop`/`pedestal` this revision, with `schema_version` next in line.

> **Consolidation (5 seeds).** Re-running the default (`coder:7b`) across seeds `42,7,1,123,999` returns **bit-identical scores** (mean 0.905 every time). Note *why*: the canonical config is greedy (`temperature=0`, `top_k=1`), so the seed does not change the output. This proves **reproducibility**, not robustness to sampling noise — measuring the latter would require `temperature > 0`, and is left as honest future work. The corpus is also small (11 cases), so per-field figures stay indicative rather than definitive.

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

---

## Roadmap

- **Grow the corpora** beyond 5 / 11 cases.
- **Cross-seed robustness**: vary the seed to measure sampling noise, not just reproducibility.
- **Extend coverage** to the router/classifier, the web-search path and the ComfyUI image pipeline (new harnesses).
- Track baselines **across models** to compare candidates objectively.
