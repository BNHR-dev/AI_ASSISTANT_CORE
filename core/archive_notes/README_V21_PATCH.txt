PATCH V2.1 — COMFYUI RUNTIME + PROMPTS/OUTPUT QUALITY

Contenu :
- app/clients/comfyui_runtime.py
- app/clients/comfyui_client.py
- app/engine/agent_prompt_registry.py
- app/engine/prompt_builder.py
- app/engine/step_executor.py
- tests/test_comfyui_runtime.py
- tests/test_prompt_quality_v21.py
- docs/PATCH_V21_COMFYUI_PROMPTS.md

But :
- auto-start ComfyUI si besoin
- améliorer la qualité des prompts multi-step
- éviter les sorties explain/build trop mélangées
- renforcer le côté copilote réel sans casser la baseline
