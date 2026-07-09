# Experiment A — results (pixel)

- Embedder: trivial baseline — 64x64 RGB flattened (12288 dims), cosine on raw pixels
- Dataset: 100 images (20 conform / 80 degraded, 5 cases)

## Primary AUC (within-case pairs): **0.762**
Pooled AUC (cross-case, transparency): 0.762

**Verdict against pre-registered thresholds: baseline — context for the learned metric; pre-registered thresholds do not apply**

| Defect | AUC | contract sees it |
|---|---|---|
| deg_framing_i1 | 0.950 | 0% |
| deg_framing_i2 | 1.000 | 0% |
| deg_framing_i3 | 1.000 | 0% |
| deg_framing | 1.000 | 0% |
| deg_intruder_i1 | 0.250 | 0% |
| deg_intruder_i2 | 0.250 | 0% |
| deg_intruder_i3 | 0.400 | 0% |
| deg_intruder | 0.600 | 0% |
| deg_nolight_i1 | 0.250 | 0% |
| deg_nolight_i2 | 0.500 | 0% |
| deg_nolight_i3 | 1.000 | 0% |
| deg_nolight | 1.000 | 100% |
| deg_rimlight_i1 | 1.000 | 0% |
| deg_rimlight_i2 | 1.000 | 0% |
| deg_rimlight_i3 | 1.000 | 0% |
| deg_rimlight | 1.000 | 0% |

| Case | AUC | conform mean | degraded mean |
|---|---|---|---|
| sf1-watch-metallic-stone-pedestal | 0.781 | 0.9994 | 0.9934 |
| v0-bottle-amber-glass-neutral-gray | 0.766 | 0.9994 | 0.9924 |
| v1-bottle-rectangular-amber-glass-cap-closeup | 0.750 | 0.9993 | 0.9899 |
| v1-jar-rounded-white-translucent-beige | 0.766 | 0.9995 | 0.9948 |
| v1-tube-cylindrical-red-opaque-warm-gray | 0.750 | 0.9994 | 0.9935 |
