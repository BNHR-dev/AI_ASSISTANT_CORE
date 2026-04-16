# ARCHITECTURE — AI_ASSISTANT_CORE

## Lecture d’ensemble

AI_ASSISTANT_CORE est un noyau d’orchestration local.

Le système prend une requête utilisateur, produit une **décision structurée**, la transforme en **plan d’exécution**, exécute ce plan étape par étape, puis renvoie une **sortie finale utile** avec une **surface d’observabilité** suffisante.

Le projet ne doit plus être décrit comme un simple backend de routing. Sa bonne abstraction est désormais :
- décision
- plan
- exécution
- assemblage
- observabilité

## Schéma canonique du noyau

```text
Utilisateur
↓
OpenWebUI / client HTTP / OpenAI-compatible API
↓
app/main.py
↓
router_service
├── task_classifier
├── TASK_ROUTING
├── routing_conditions
└── tool_selector
↓
plan_builder / planner_service
↓
step_executor
├── llm_primary
├── llm_secondary
├── tool_web_search
├── llm_synthesis
├── prepare_visual
└── tool_comfyui
↓
result_assembler
↓
ExecuteResponse / réponse finale
```

## Couches réelles

### 1. Entrée API
- `app/main.py`
- `openai_compat.py`

Rôle : exposer la surface FastAPI et la compatibilité OpenAI minimale pour OpenWebUI.

### 2. Compréhension et décision
- `app/task_classifier.py`
- `app/tool_selector.py`
- `app/engine/task_routing.py`
- `app/engine/routing_conditions.py`
- `app/engine/router_service.py`

Rôle : reconnaître la tâche, sélectionner agent/modèle/tool, appliquer les règles hybrides limitées, et produire une décision finale lisible.

### 3. Planification
- `app/engine/planner_types.py`
- `app/engine/plan_builder.py`
- `app/engine/planner_service.py`
- `app/engine/state_store.py`

Rôle : convertir la décision en `ExecutionPlan` explicite.

### 4. Exécution
- `app/engine/step_executor.py`
- `app/engine/executor.py`

Rôle : exécuter les steps dans l’ordre, tracer les résultats et remonter les métadonnées utiles.

### 5. Assemblage
- `app/engine/result_assembler.py`
- `app/engine/output_contracts.py`

Rôle : garder la sortie finale utile et masquer le bruit technique intermédiaire côté utilisateur.

### 6. Runtime et observabilité
- `app/engine/runtime_debug.py`
- `app/infra/tool_manager.py`

Rôle : exposer la santé runtime et les frontières canoniques, sans rajouter une logique métier cachée.

## Stratégies d’exécution

Le planner produit actuellement quatre stratégies réelles :
- `single_step`
- `two_step_llm`
- `web_pipeline`
- `visual_pipeline`

## Architecture de déploiement post-VM

### Noyau produit
Le noyau produit reste :
- routeur
- planner
- executor
- observabilité

La VM n’ajoute pas une nouvelle logique métier. Elle ajoute une **forme de déploiement canonique** et une **frontière d’isolation**.

### Runtime produit
#### Dans la VM Hyper-V `AICORE-VM`
- backend AI_ASSISTANT_CORE
- service `aicore-backend.service`
- bind `127.0.0.1:8000`
- SearXNG
- service `aicore-searxng.service`
- bind `127.0.0.1:8081 -> 8080`

#### Sur le host Windows
- Ollama
- ComfyUI
- éventuellement OpenWebUI comme UI opérateur distincte du runtime principal

### Frontière de sécurité produit
- host Windows
- VM Hyper-V isolée
- réseau privé `AICORE-INT`
- host : `192.168.77.1`
- VM : `192.168.77.10`
- flux utiles uniquement :
  - VM → Ollama host : `192.168.77.1:12001`
  - VM → ComfyUI host : `192.168.77.1:8188`

Dans le setup actuel, le raccord VM → Ollama repose sur un `portproxy` Windows `192.168.77.1:12001 -> 127.0.0.1:12000`.

Le chemin direct VM → `192.168.77.1:12000` ne doit plus être documenté comme invariant runtime validé.

## Sous-système visuel après session 4

La session 4 a raffiné le pipeline visuel sans modifier le noyau général.

### Analyse d’intention visuelle
Le système analyse maintenant :
- `subject_type` : `portrait`, `product`, `scene`
- `render_intent` : `standard`, `packshot`, `poster`, `cover`, `key_visual`
- `style_flags` : signaux de style utiles pour enrichir le prompt

### Sélection de workflow
Mapping actuel :
- `portrait` → `portrait_basic_v1`
- `product` → `object_basic_v1`
- `scene` → `cinematic_scene_v1`

### Enrichissement de prompt visuel
`app/clients/comfyui_client.py` enrichit le prompt positif selon :
- le sujet
- l’intention de rendu
- les flags de style
- le workflow retenu

### Variantes et succès partiel
Le pipeline visuel remonte maintenant :
- `artifact_path` et `artifact_filename`
- `artifact_paths` et `artifact_filenames`
- `workflow_id`
- `variants_count`
- `completed_variants`
- `partial_visual_success`
- `comfyui_status`
- `comfyui_prompt_id`

## API et surface externe

### FastAPI canonique
- `GET /health`
- `GET /health/runtime`
- `GET /debug/canonical`
- `POST /route`
- `POST /execute`

### OpenAI-compatible
- `GET /v1/models`
- `POST /v1/chat/completions`

Cette couche sert d’interface avec OpenWebUI sans couplage spécifique à l’outil.

## Canonique vs legacy

Source de vérité :
1. `app/*`
2. `openai_compat.py`
3. docs `docs/*`

Shims legacy confirmés à la racine :
- `executor.py`
- `router_service.py`
- `task_classifier.py`
- `tool_selector.py`
- `task_routing.py`
- `comfyui_client.py`

Ces fichiers ne doivent pas redevenir des sources métier.

## Gaps structurels encore réels

- `portproxy` Ollama encore transitoire dans sa forme, même s’il est canonique dans le setup actuel
- pas encore de mode public dédié au visuel dans `/v1/models`
- surface `/debug/canonical` à surveiller tant qu’elle peut encore marquer certains modules actifs comme “dormant”
- dette legacy encore présente même si contenue
- statut produit final d’OpenWebUI encore à décider explicitement

## Décision d’architecture à préserver

Le projet doit continuer à évoluer comme un noyau **routeur + planner + executor**, avec améliorations incrémentales, testables et visibles, plutôt que par grands refactors ou nouvelles surcouches prématurées.
