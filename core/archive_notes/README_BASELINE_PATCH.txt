PATCH BASELINE V1.7 STABLE → PROTO V2 CONTRÔLÉ

Contenu :
- manual_runner.py (remplacement)
- tests/test_baseline_v17_regression.py
- tests/test_openai_compat_modes.py
- docs/BASELINE_V1_7_STABLE.md
- docs/NEXT_STEP_PROTO_V2.md

But :
- figer une baseline fiable
- ajouter les vrais tests de non-régression métier
- valider le mapping UI OpenWebUI → modes backend
- éviter de repartir dans un refactor flou

Ordre :
1. remplace manual_runner.py
2. ajoute les deux nouveaux tests
3. ajoute les deux docs
4. lance la baseline complète
