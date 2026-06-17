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
- pipeline Blender expérimental (génération de scènes 3D, headless sur le host)
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

Stratégies d'exécution réellement présentes :
- `single_step`
- `two_step_llm`
- `web_pipeline`
- `visual_pipeline`
- `blender_pipeline`

Le projet doit donc être documenté comme :
**routeur + planner + executor**, pas comme un backend de réponse unique.

## Architecture de déploiement (single-host, localhost)

Le runtime canonique est **single-host** : tout tourne sur la même machine et communique en `localhost` (`127.0.0.1`). La migration depuis l'ancienne topologie — un invité **Ubuntu/Linux** sur hôte **Windows** (Hyper-V) — est clôturée ; ce contexte est archivé sous `infra/vm/` et **ne fait pas partie** du runtime canonique.

### Backend (sur le host)
- backend AI_ASSISTANT_CORE (FastAPI), bind `127.0.0.1:8000`

### Services en conteneur (`docker-compose.linux.yml`, ports bornés à `127.0.0.1`)
- Ollama — LLM local (`127.0.0.1:${OLLAMA_PORT} -> 11434`)
- SearXNG — recherche web (`127.0.0.1:8081 -> 8080`)
- OpenWebUI (optionnel) — UI opérateur, **hors runtime canonique** (non requis pour le cœur du produit)

### Hors conteneur (sur le host)
- ComfyUI — service supposé déjà joignable en `127.0.0.1:8188`
- Blender — exécuté **headless directement sur le host** (GPU NVIDIA)

**Invariants runtime — ports canoniques (tous sur `127.0.0.1`) :**

| Service | Port hôte |
|---|---|
| Backend AI_ASSISTANT_CORE (FastAPI) | `8000` |
| Ollama | `12000` → conteneur `11434` |
| SearXNG | `8081` → conteneur `8080` |
| ComfyUI | `8188` |
| OpenWebUI (optionnel) | `8088` |

> **Isolation.** Le sandboxing de l'exécution du code généré reste un **objectif produit** (audit 2026-06-10, finding C1) ; il n'est plus porté par une VM aujourd'hui et ne doit pas être présenté comme une isolation déjà en place.
>
> **Roadmap.** Direction visée : isoler l'exécution du code généré dans une **VM d'isolation dédiée** (Linux, sur le host), pour les pipelines de studios d'animation 3D manipulant des assets confidentiels. Distincte de l'ancienne topologie Hyper-V archivée.

### Setup (Linux / Windows)
- **Linux (Fedora — chemin validé)** : `cp core/.env.example core/.env`, puis `docker compose -f core/docker-compose.linux.yml up -d` (Ollama / SearXNG / OpenWebUI ; GPU natif via `nvidia-container-toolkit`). Lancer ensuite le backend FastAPI sur `127.0.0.1:8000` (mise en place détaillée : `docs/SETUP_LINUX.md`).
- **Windows (Docker Desktop)** : mêmes endpoints `localhost` via `docker compose -f core/docker-compose.yml up -d` ; GPU passé par le **backend WSL2** ; pour ComfyUI, un launcher `.bat` au lieu du `.sh`.

## API exposée

Endpoints FastAPI canoniques :
- `GET /health`
- `GET /health/runtime`
- `GET /debug/canonical`
- `POST /route`
- `POST /execute`

Couche OpenAI-compatible :
- `GET /v1/models` — 8 model cards statiques ; model ID inconnu → fallback silencieux `auto`
- `POST /v1/chat/completions` — `choices[0].message.content` est **toujours une string**

Model IDs disponibles :

| Model ID | Mode |
|---|---|
| `assistant-core-auto` | `auto` |
| `assistant-core-prof` | `explain` |
| `assistant-core-builder` | `build` |
| `assistant-core-archi` | `architecture` |
| `assistant-core-exam` | `critique` |
| `assistant-core-vision` | `vision` |
| `assistant-core-image` | `image_generation` |
| `assistant-core-web` | `web_research` |

Pour `image_generation`, les artefacts image sont intégrés dans `content` sous forme de markdown data-URI lorsque l'image est récupérable et que son `Content-Type` est `image/*` :
- **Branche HTTP** (canonique) : `artifact_view_url(s)` → ComfyUI `/view` → `![filename](data:<mime>;base64,...)`
- **Branche locale** (fallback host-only) : `artifact_path(s)` → lecture filesystem → même embed
- `MAX_EMBED_IMAGES = 4` — `MAX_EMBED_BYTES_PER_IMAGE = 4 MiB`
- `COMFYUI_VIEW_TIMEOUT` configurable via env var (défaut 15s)
- `Content-Type` non-image depuis `/view` → rejeté, fallback texte

## Pipeline Blender expérimental

AI_ASSISTANT_CORE dispose désormais d'un pipeline Blender expérimental mais fonctionnel. Pour les demandes de création Blender, le backend peut générer un script `scene.py`, exécuter Blender headless sur le host, produire un artefact canonique `scene.blend` et générer un `preview.png` best-effort. Le preview est produit dans un subprocess séparé afin de ne pas polluer le script principal et de garder le fichier `.blend` comme artefact de référence.

Points clés :
- `scene.blend` est l'artefact canonique
- `preview.png` est best-effort et ne doit pas rendre le pipeline global bloquant
- `/health/runtime` peut rester `partial` si ComfyUI est indisponible, sans bloquer Blender
- les fichiers sont produits sous `outputs/blender/<uuid>/`

## Gaps encore réels

Le projet est solide au niveau du noyau, mais pas encore totalement nettoyé au niveau runtime et surface externe.

Gaps visibles à garder en tête :
- l'isolation/sandbox de l'exécution du code généré reste un **objectif produit** non livré (audit 2026-06-10, C1) — à ne pas surreprésenter comme acquis
- OpenWebUI acté comme UI opérateur optionnelle côté host, non canonique et non requise pour le fonctionnement du cœur du produit
- la surface `/debug/canonical` doit continuer à refléter correctement la frontière entre modules actifs, auxiliaires, optionnels et legacy
- les fichiers racine legacy existent encore et doivent rester de simples shims de compatibilité

## Structure utile du repo

```text
core/
├── app/
├── docs/
│   ├── ARCHITECTURE.md
│   ├── ROADMAP.md
│   └── SETUP_LINUX.md
├── scripts/
├── tests/
├── openai_compat.py
└── README.md   ← présent uniquement à la racine (convention GitHub)
```

Les docs détaillées vivent sous `docs/`. Le README à la racine sert d'entrée et pointe vers `docs/` pour l'architecture, la roadmap et le setup.

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
