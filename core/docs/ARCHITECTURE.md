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

## Stratégies d'exécution

Le planner produit actuellement cinq stratégies réelles :
- `single_step`
- `two_step_llm`
- `web_pipeline`
- `visual_pipeline`
- `blender_pipeline`

## Architecture de déploiement post-VM

### Noyau produit
Le noyau produit reste :
- routeur
- planner
- executor
- observabilité

Le déploiement single-host n'ajoute pas de logique métier. L'isolation de l'exécution du code généré reste un **objectif produit** (audit 2026-06-10, C1), aujourd'hui non livrée — à ne pas présenter comme une frontière déjà en place.

### Runtime produit (single-host, localhost)
Tout le runtime canonique tourne sur une seule machine et communique en `localhost` (`127.0.0.1`). L'ancienne topologie — un invité Ubuntu/Linux sur hôte Windows (Hyper-V) — est archivée sous `infra/vm/`, hors runtime canonique.

#### Sur le host
- backend AI_ASSISTANT_CORE (FastAPI), bind `127.0.0.1:8000`
- ComfyUI — supposé joignable en `127.0.0.1:8188`
- Blender — exécuté headless directement sur le host (GPU NVIDIA)

#### En conteneur (`docker-compose.linux.yml`, ports bornés à `127.0.0.1`)
- Ollama — LLM local (`127.0.0.1:${OLLAMA_PORT} -> 11434`)
- SearXNG — recherche web (`127.0.0.1:8081 -> 8080`)
- OpenWebUI (optionnel) — UI opérateur, **hors runtime canonique** (non requis pour le cœur du produit)

### Frontière de sécurité produit
- déploiement single-host : pas de frontière d'isolation réseau dédiée aujourd'hui
- l'isolation de l'exécution du code généré reste un **objectif produit** (audit 2026-06-10, C1), non livré — à ne pas surreprésenter comme acquis
- **roadmap** : isoler l'exécution du code généré dans une **VM d'isolation dédiée** (Linux, sur le host), motivée par la confidentialité des assets studio — distincte de l'ancienne topologie Hyper-V archivée

Les ports/binds canoniques sont ceux listés ci-dessus ; tous les services écoutent sur `127.0.0.1` (backend `8000`, Ollama `12000`, SearXNG `8081`, ComfyUI `8188`, OpenWebUI optionnel `8088`).

## Sous-système Blender (pipeline expérimental)

Le pipeline Blender est rattaché à la couche clients/tools sans modifier le noyau routeur + planner + executor.

- `app/clients/blender_client.py` — exécution Blender headless sur le host
- le planner/executor existant route vers `blender_pipeline` pour les demandes 3D
- les fichiers sont produits sous `outputs/blender/<uuid>/` :
  - `scene.py` — script bpy généré
  - `scene.blend` — artefact canonique
  - `preview.png` — rendu best-effort, généré dans un subprocess séparé
- le rendu preview est isolé dans un second subprocess pour ne pas polluer le script principal et garantir que `scene.blend` reste l'artefact de référence
- `preview.png` ne doit pas rendre le pipeline global bloquant
- `/health/runtime` peut rester `partial` si ComfyUI est indisponible sans bloquer Blender

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

- isolation/sandbox de l'exécution du code généré : **objectif produit** non livré (audit 2026-06-10, C1)
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
