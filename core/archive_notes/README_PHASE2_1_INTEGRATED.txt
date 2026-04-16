AI_ASSISTANT_CORE — PHASE 2.1 INTÉGRÉE

Cette archive contient :
- le socle Phase 1 stabilisé
- l'ajout Phase 2.1 pour ComfyUI
- les nouveaux types visuels
- le sélecteur de workflow visuel
- le client ComfyUI HTTP
- les templates JSON starter
- les tests Phase 1 + Phase 2.1

Intégration principale :
- app/engine/executor.py
- app/clients/comfyui_client.py
- app/engine/visual_types.py
- app/engine/visual_workflow_selector.py
- app/workflows/comfyui/*.json

Tests inclus :
- test_real_world_cases.py
- test_routing_conditions.py
- test_router_executor.py
- test_comfyui_client.py
- test_visual_pipeline.py
- test_executor_visual_phase2.py
