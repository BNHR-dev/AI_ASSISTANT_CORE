# AI_ASSISTANT_CORE

AI_ASSISTANT_CORE est un orchestrateur IA local orienté création numérique.

Le projet ne doit plus être lu comme un simple routeur minimal. Son noyau réel est maintenant :
- un routeur de décision
- un planner explicite
- un executor step-by-step
- une surface d’observabilité utile

La baseline exposée par l’API reste **V1.7.0**, mais le snapshot documenté ici est désormais **réaligné après validation de la session 4, de la stabilisation runtime post-VM et de la phase 1 de hardening safe backend**.

## Ce que le projet sait réellement faire

Le noyau actuel sait :
- classifier une demande en `task_type`
- choisir un agent, un modèle et un tool si nécessaire
- construire un plan d’exécution explicite
- exécuter ce plan étape par étape
- assembler une sortie finale propre
- exposer une trace suffisante pour relire la décision

Capacités présentes dans le snapshot :
- explication simple et avancée
- build orienté code
- critique
- architecture
- recherche web via SearXNG + synthèse LLM
- vision via `qwen2.5vl:3b`
- génération visuelle via ComfyUI
- compatibilité OpenAI pour OpenWebUI
- santé runtime et frontières canoniques via API

## Apports visuels intégrés en session 4

La session 4 a ajouté une petite couche d’intention visuelle **avant** ComfyUI, sans modifier le noyau routeur + planner + executor.

Concrètement, le pipeline visuel sait maintenant :
- analyser le type de sujet : `portrait`, `product`, `scene`
- analyser l’intention de rendu : `standard`, `packshot`, `poster`, `cover`, `key_visual`
- détecter des `style_flags` utiles comme `cyberpunk`, `sci_fi`, `neon`, `rainy`, `luxury`, `studio`, `cinematic`
- sélectionner un workflow de manière plus structurée tout en gardant la façade legacy `select_visual_workflow()`
- enrichir le prompt positif en fonction de cette analyse
- appliquer des presets simples de format selon le rendu
- exposer des métadonnées visuelles enrichies dès `prepare_visual`
- gérer proprement plusieurs variantes et les cas de succès partiel

Workflows actuellement utilisés :
- `portrait_basic_v1`
- `object_basic_v1`
- `cinematic_scene_v1`

## Lecture correcte de l’architecture

Flux canonique :
1. `task_classifier`
2. `TASK_ROUTING`
3. `routing_conditions`
4. `tool_selector`
5. `router_service`
6. `plan_builder`
7. `step_executor`
8. `result_assembler`
9. `executor`

Stratégies d’exécution réellement présentes :
- `single_step`
- `two_step_llm`
- `web_pipeline`
- `visual_pipeline`

Le projet doit donc être documenté comme :
**routeur + planner + executor**, pas comme un backend de réponse unique.

## Architecture de déploiement post-VM

### Dans la VM Hyper-V `AICORE-VM`
- backend AI_ASSISTANT_CORE
- `aicore-backend.service`
- bind `127.0.0.1:8000`
- SearXNG
- `aicore-searxng.service`
- bind `127.0.0.1:8081`

### Sur le host Windows
- Ollama
- ComfyUI
- OpenWebUI, optionnel, comme UI opérateur distincte du runtime principal (non canonique, non requis)

### Réseau et isolation
- réseau privé Hyper-V `AICORE-INT`
- host : `192.168.77.1`
- VM : `192.168.77.10`
- backend VM → Ollama host : `http://192.168.77.1:12001`
- backend VM → ComfyUI host : `http://192.168.77.1:8188`

Dans le setup validé ici, l’accès VM → Ollama repose sur un `portproxy` Windows `192.168.77.1:12001 -> 127.0.0.1:12000`.

La VM n’est donc plus un simple environnement de dev : elle porte déjà le runtime principal du produit et sa première couche de sécurité structurelle.

## API exposée

Endpoints FastAPI canoniques :
- `GET /health`
- `GET /health/runtime`
- `GET /debug/canonical`
- `POST /route`
- `POST /execute`

Couche OpenAI-compatible :
- `GET /v1/models`
- `POST /v1/chat/completions`

## Gaps encore réels

Le projet est solide au niveau du noyau, mais pas encore totalement nettoyé au niveau runtime et surface externe.

Gaps visibles à garder en tête :
- le `portproxy` Ollama est **canonique à court terme** mais encore **transitoire dans sa forme**
- le chemin direct VM → `192.168.77.1:12000` ne doit plus être documenté comme chemin runtime validé
- OpenWebUI acté comme UI opérateur optionnelle côté host, non canonique et non requise pour le runtime principal
- il n’existe pas encore de mode public dédié à `image_generation` ou `vision` côté `/v1/models`
- la surface `/debug/canonical` doit continuer à refléter correctement la frontière entre modules actifs, auxiliaires, optionnels et legacy
- les fichiers racine legacy existent encore et doivent rester de simples shims de compatibilité

## Structure utile du repo

```text
core/
├── app/
├── docs/
├── scripts/
├── tests/
├── openai_compat.py
├── README.md
├── ROADMAP.md
├── ARCHITECTURE.md
└── RUNBOOK_POST_VM.md
```

## Tests et validation

Validation de référence à la sortie de la session 4 :
- batterie visuelle étendue : `29 passed`
- release gate smoke : `26 passed`
- release gate core : `45 passed`

Validation runtime post-VM :
- backend VM lancé via `systemd`
- SearXNG VM lancé via `systemd` + Docker
- `/health` : OK
- `/health/runtime` : OK
- `/route` : OK
- `/execute` : OK

Le projet peut donc être lu comme un noyau documentable et stable, avec une dette surtout documentaire, runtime et legacy — plus comme un proto flou.

## Discipline de lecture

Quand une information diverge :
1. `app/*` prime
2. `openai_compat.py` prime pour l’intégration UI
3. les docs `docs/*` servent de récit canonique du snapshot
4. les fichiers racine legacy ne sont pas des sources métier
