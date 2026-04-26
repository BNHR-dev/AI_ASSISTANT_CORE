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
- service Docker `searxng` (`restart: unless-stopped`)
- bind `127.0.0.1:8081 -> 8080`

#### Sur le host Windows
- Ollama
- ComfyUI
- OpenWebUI (optionnel) comme UI opérateur, **hors runtime canonique** — voir « Décision OpenWebUI » dans `docs/RUNBOOK_POST_VM.md`

### Frontière de sécurité produit
- host Windows
- VM Hyper-V isolée
- réseau privé `AICORE-INT`
- host : `192.168.77.1`
- VM : `192.168.77.10`
- flux utiles : VM → Ollama host, VM → ComfyUI host

Ports, binds et URL canoniques : voir la section **Invariants runtime (référence canonique)** dans `docs/RUNBOOK_POST_VM.md`. Cette architecture ne les redéfinit pas pour éviter toute dérive.

Dans le setup actuel, le raccord VM → Ollama repose sur un `portproxy` Windows. Ce `portproxy` est une **dépendance runtime canonique à court terme** mais **transitoire dans sa forme** — pas un invariant final de topologie. Le chemin direct VM → `192.168.77.1:12000` ne doit pas être documenté comme runtime validé.

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
- `GET /v1/models` — 8 model cards statiques (`MODEL_TO_MODE`) ; model ID inconnu → fallback `auto`
- `POST /v1/chat/completions` — `choices[0].message.content` est **toujours une string** (Phase 5)

La couche sert d’interface avec OpenWebUI sans couplage spécifique à l’outil.

Pour les résultats `artifact_type == "image"`, le content est un markdown data-URI lorsque l’image est récupérable et son `Content-Type` est `image/*` :
- **branche HTTP** (`artifact_view_url(s)`) : téléchargement depuis ComfyUI `/view` → `![filename](data:<mime>;base64,...)`
- **branche locale** (`artifact_path(s)`) : lecture filesystem → même embed
- `Content-Type` non-image → rejeté, fallback texte "non récupérable depuis ComfyUI"
- `MAX_EMBED_IMAGES = 4` — `MAX_EMBED_BYTES_PER_IMAGE = 4 MiB` — `COMFYUI_VIEW_TIMEOUT` env var (défaut 15s)

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

- `portproxy` Ollama **canonique à court terme** mais **transitoire dans sa forme** (formulation unique partagée avec `README.md` et `docs/RUNBOOK_POST_VM.md`)
- dette legacy encore présente même si contenue
- OpenWebUI acté comme UI opérateur optionnelle côté host, non canonique et non requise pour le fonctionnement du cœur du produit

## Surface `/debug/canonical` et classification des modules

La surface `/debug/canonical` expose la classification canonique du code `app/*` en trois listes :
- `ACTIVE_RUNTIME_MODULES` — code qui porte le flux **décision → plan → exécution → sortie**
- `ACTIVE_AUXILIARY_MODULES` — code technique de support utilisé **par** le runtime sans appartenir au flux (clients, healthchecks, URLs)
- `DORMANT_MODULES` — présent dans le repo mais non importé par le flux réel (internes supplantés, snapshots legacy, helpers inutilisés)

Les trois listes sont définies dans `app/engine/runtime_debug.py` et doivent former un découpage **exhaustif et disjoint** de tous les `app/*.py` (hors `__init__.py`).

### Verrouillage structurel

Cette classification est verrouillée par `tests/test_runtime_debug_classification.py`, qui vérifie sans mock :
- chaque module listé existe réellement sur le disque
- les trois listes sont disjointes deux à deux
- chaque `app/*.py` (hors `__init__.py`) figure dans **exactement une** des trois listes
- les modules critiques du flux (`task_classifier`, `router_service`, `planner_service`, `plan_builder`, `executor`, `step_executor`, `result_assembler`, etc.) restent dans `ACTIVE_RUNTIME_MODULES`
- les shims legacy de racine ne fuient pas dans les listes `app/*`
- le payload de `get_canonical_boundaries()` expose bien les constantes du module

Conséquence : tout nouveau fichier sous `app/` qui n'est classifié ni runtime, ni auxiliaire, ni dormant fait échouer les tests avec un message clair nommant le fichier. Cela empêche la dérive silencieuse entre ce qui vit réellement dans le code et ce que la surface debug rapporte.

## Décision d’architecture à préserver

Le projet doit continuer à évoluer comme un noyau **routeur + planner + executor**, avec améliorations incrémentales, testables et visibles, plutôt que par grands refactors ou nouvelles surcouches prématurées.
