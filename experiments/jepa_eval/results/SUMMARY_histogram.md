# Experiment A — results (histogram)

- Embedder: trivial baseline — 32-bin per-channel RGB color histogram (96 dims), cosine
- Dataset: 40 images (20 conform / 20 degraded, 5 cases)

## Primary AUC (within-case pairs): **0.950**
Pooled AUC (cross-case, transparency): 0.950

**Verdict against pre-registered thresholds: baseline — context for the learned metric; pre-registered thresholds do not apply**

| Defect | AUC | contract sees it |
|---|---|---|
| deg_nolight | 1.000 | 100% |
| deg_framing | 1.000 | 0% |
| deg_intruder | 0.800 | 0% |
| deg_rimlight | 1.000 | 0% |

| Case | AUC | conform mean | degraded mean |
|---|---|---|---|
| sf1-watch-metallic-stone-pedestal | 1.000 | 0.9952 | 0.7376 |
| v0-bottle-amber-glass-neutral-gray | 0.938 | 0.9963 | 0.7115 |
| v1-bottle-rectangular-amber-glass-cap-closeup | 0.938 | 0.9957 | 0.7136 |
| v1-jar-rounded-white-translucent-beige | 0.938 | 0.9967 | 0.7101 |
| v1-tube-cylindrical-red-opaque-warm-gray | 0.938 | 0.9959 | 0.7139 |
