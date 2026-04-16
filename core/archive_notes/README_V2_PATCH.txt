AI_ASSISTANT_CORE — PATCH V2 MULTI-STEP

Contenu :
- app/engine/planner_types.py
- app/engine/plan_builder.py
- app/engine/planner_service.py
- app/engine/state_store.py
- app/engine/step_executor.py
- app/engine/result_assembler.py
- app/engine/fallbacks.py
- app/engine/executor.py (remplacement)
- tests/test_planner_service.py
- tests/test_multistep_executor.py

Objectif :
- remplacer le second_call implicite par un vrai plan multi-step
- garder router_service comme point de décision central
- exécuter les étapes une par une
- assembler un résultat final propre
- exposer plan + step_results + decision_trace enrichie

Commandes de test :
python -m pytest tests/test_planner_service.py tests/test_multistep_executor.py -v
python manual_runner.py
