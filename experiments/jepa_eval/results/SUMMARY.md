# Experiment A — results

- Encoder: `vjepa2_1_vit_large_384` (frozen, commit `204698b45b37`), 384px, mean-pooled
- Dataset: 40 images (20 conform / 20 degraded, 5 cases)

## Primary AUC (within-case pairs): **1.000**
Pooled AUC (cross-case, transparency): 1.000

**Verdict against pre-registered thresholds: real signal (AUC >= 0.80) — keep the metric, document it**

| Defect | AUC | contract sees it |
|---|---|---|
| deg_nolight | 1.000 | 100% |
| deg_framing | 1.000 | 0% |
| deg_intruder | 1.000 | 0% |
| deg_rimlight | 1.000 | 0% |

| Case | AUC | conform mean | degraded mean |
|---|---|---|---|
| sf1-watch-metallic-stone-pedestal | 1.000 | 0.9983 | 0.9674 |
| v0-bottle-amber-glass-neutral-gray | 1.000 | 0.9986 | 0.9685 |
| v1-bottle-rectangular-amber-glass-cap-closeup | 1.000 | 0.9986 | 0.9692 |
| v1-jar-rounded-white-translucent-beige | 1.000 | 0.9989 | 0.9668 |
| v1-tube-cylindrical-red-opaque-warm-gray | 1.000 | 0.9989 | 0.9676 |
