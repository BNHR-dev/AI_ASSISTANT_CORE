# Experiment A — results (histogram)

- Embedder: trivial baseline — 32-bin per-channel RGB color histogram (96 dims), cosine
- Dataset: 100 images (20 conform / 80 degraded, 5 cases)

## Primary AUC (within-case pairs): **0.806**
Pooled AUC (cross-case, transparency): 0.811

**Verdict against pre-registered thresholds: baseline — context for the learned metric; pre-registered thresholds do not apply**

| Defect | AUC | contract sees it |
|---|---|---|
| deg_framing_i1 | 0.450 | 0% |
| deg_framing_i2 | 0.950 | 0% |
| deg_framing_i3 | 1.000 | 0% |
| deg_framing | 1.000 | 0% |
| deg_intruder_i1 | 0.000 | 0% |
| deg_intruder_i2 | 0.150 | 0% |
| deg_intruder_i3 | 0.550 | 0% |
| deg_intruder | 0.800 | 0% |
| deg_nolight_i1 | 1.000 | 0% |
| deg_nolight_i2 | 1.000 | 0% |
| deg_nolight_i3 | 1.000 | 0% |
| deg_nolight | 1.000 | 100% |
| deg_rimlight_i1 | 1.000 | 0% |
| deg_rimlight_i2 | 1.000 | 0% |
| deg_rimlight_i3 | 1.000 | 0% |
| deg_rimlight | 1.000 | 0% |

| Case | AUC | conform mean | degraded mean |
|---|---|---|---|
| sf1-watch-metallic-stone-pedestal | 0.812 | 0.9952 | 0.7984 |
| v0-bottle-amber-glass-neutral-gray | 0.812 | 0.9963 | 0.7873 |
| v1-bottle-rectangular-amber-glass-cap-closeup | 0.812 | 0.9957 | 0.7892 |
| v1-jar-rounded-white-translucent-beige | 0.797 | 0.9967 | 0.7882 |
| v1-tube-cylindrical-red-opaque-warm-gray | 0.797 | 0.9959 | 0.7945 |
