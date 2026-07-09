# Experiment A — results (pixel)

- Embedder: trivial baseline — 64x64 RGB flattened (12288 dims), cosine on raw pixels
- Dataset: 40 images (20 conform / 20 degraded, 5 cases)

## Primary AUC (within-case pairs): **0.900**
Pooled AUC (cross-case, transparency): 0.897

**Verdict against pre-registered thresholds: baseline — context for the learned metric; pre-registered thresholds do not apply**

| Defect | AUC | contract sees it |
|---|---|---|
| deg_nolight | 1.000 | 100% |
| deg_framing | 1.000 | 0% |
| deg_intruder | 0.600 | 0% |
| deg_rimlight | 1.000 | 0% |

| Case | AUC | conform mean | degraded mean |
|---|---|---|---|
| sf1-watch-metallic-stone-pedestal | 0.938 | 0.9994 | 0.9889 |
| v0-bottle-amber-glass-neutral-gray | 0.875 | 0.9994 | 0.9863 |
| v1-bottle-rectangular-amber-glass-cap-closeup | 0.875 | 0.9993 | 0.9831 |
| v1-jar-rounded-white-translucent-beige | 0.938 | 0.9995 | 0.9899 |
| v1-tube-cylindrical-red-opaque-warm-gray | 0.875 | 0.9994 | 0.9874 |
