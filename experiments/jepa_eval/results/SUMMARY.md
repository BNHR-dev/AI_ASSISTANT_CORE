# Experiment A — results (vjepa)

- Embedder: `vjepa2_1_vit_large_384` (frozen, commit `204698b45b37`), 384px, mean-pooled
- Dataset: 100 images (20 conform / 80 degraded, 5 cases)

## Primary AUC (within-case pairs): **0.975**
Pooled AUC (cross-case, transparency): 0.973

**Verdict against pre-registered thresholds: real signal (AUC >= 0.80) — keep the metric, document it**

| Defect | AUC | contract sees it |
|---|---|---|
| deg_framing_i1 | 1.000 | 0% |
| deg_framing_i2 | 1.000 | 0% |
| deg_framing_i3 | 1.000 | 0% |
| deg_framing | 1.000 | 0% |
| deg_intruder_i1 | 0.850 | 0% |
| deg_intruder_i2 | 0.950 | 0% |
| deg_intruder_i3 | 1.000 | 0% |
| deg_intruder | 1.000 | 0% |
| deg_nolight_i1 | 0.800 | 0% |
| deg_nolight_i2 | 1.000 | 0% |
| deg_nolight_i3 | 1.000 | 0% |
| deg_nolight | 1.000 | 100% |
| deg_rimlight_i1 | 1.000 | 0% |
| deg_rimlight_i2 | 1.000 | 0% |
| deg_rimlight_i3 | 1.000 | 0% |
| deg_rimlight | 1.000 | 0% |

| Case | AUC | conform mean | degraded mean |
|---|---|---|---|
| sf1-watch-metallic-stone-pedestal | 0.969 | 0.9983 | 0.9779 |
| v0-bottle-amber-glass-neutral-gray | 0.969 | 0.9986 | 0.9783 |
| v1-bottle-rectangular-amber-glass-cap-closeup | 0.953 | 0.9986 | 0.9786 |
| v1-jar-rounded-white-translucent-beige | 0.984 | 0.9989 | 0.9783 |
| v1-tube-cylindrical-red-opaque-warm-gray | 1.000 | 0.9989 | 0.9787 |
