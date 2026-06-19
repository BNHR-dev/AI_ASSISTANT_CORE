# Docker — « tourne partout en une commande »

> But : `docker compose up` démarre la stack AAC complète sur **Windows / macOS / Linux**,
> pour qu'un évaluateur lance le projet **sans environnement Linux ni installation manuelle**.
>
> Le runtime de **production / dev** reste **Linux natif** (plus rapide, isolation `bwrap`,
> rendu EEVEE GPU). Docker est le chemin de **portée** (reachability), pas le runtime de prod.

## Les 4 niveaux de portée
1. **Vidéo / démo hébergée** — le recruteur ne lance rien, il voit que ça marche.
2. **`docker compose up`** — l'évaluateur technique lance la stack en une commande (ce document).
3. **WSL2** — environnement Linux complet dans Windows.
4. **Linux natif** — runtime de production.

## Topologie cible
| Service | Image | Rôle | GPU |
|---|---|---|---|
| `aac-backend` | construite (`Dockerfile`) | FastAPI + Blender + bwrap, spawn Blender en local | optionnel |
| `comfyui` | construite (P2) | serveur ComfyUI `:8188`, modèles via volume | optionnel |
| `ollama` | `ollama/ollama` | LLM `:11434` | optionnel |
| `searxng` | `searxng/searxng` | recherche `:8080` | non |

Réseau interne compose : le backend appelle les autres par **nom de service**
(`http://ollama:11434`, `http://searxng:8080`, `http://comfyui:8188`). Seul le backend
est exposé sur l'hôte (`127.0.0.1:8000`).

## GPU optionnel, fallback CPU (la clé cross-platform)
- **Base** (`docker-compose.app.yml`) = **CPU-safe**, marche partout (même sans GPU).
- **Overlay** (`docker-compose.gpu.yml`, P3) = ajoute les réservations NVIDIA.
  - Linux + NVIDIA → `docker compose -f docker-compose.app.yml -f docker-compose.gpu.yml up`
  - Windows + NVIDIA (Docker Desktop, backend WSL2) → même overlay (CUDA via WSL2)
  - macOS / sans GPU → base seule = CPU (lent mais fonctionnel)

## Décisions de design (actées)
1. **Blender est dans le backend**, pas un service séparé : le backend le `subprocess`
   en local (comme en natif). Pas de refacto en service réseau.
2. **Rendu en conteneur = Cycles** (CPU/GPU). EEVEE-headless-GPU reste une capacité
   **Linux-hôte natif** ; le démo Docker rend en Cycles.
3. **bwrap dans un conteneur** : le conteneur **est déjà** une frontière d'isolation.
   Démo → `AAC_BLENDER_SANDBOX=auto` (dégradé toléré). Durcissement → conteneur avec les
   capacités pour bwrap imbriqué (documenté). On n'affirme pas « conteneur == bwrap ».
4. **Modèles hors image** (RealVisXL ~7 Go, ESRGAN, modèles Ollama) : montés en volume,
   jamais cuits dans l'image. Démo **pleine** : RealVisXL + refiner + ESRGAN.

## Phases
- **P1 — Backend conteneurisé** *(fait)*
  - **P1a** *(fait)* : `Dockerfile` backend (Python 3.14 + `requirements.txt` + app), `up`
    → `/health` ok, parle à Ollama/SearXNG. CPU.
  - **P1b** *(fait)* : Blender **5.1.1** + **bubblewrap** dans l'image. Validé : `blender
    --version` + rendu headless **Cycles CPU** OK dans le conteneur. *(bwrap installé ;
    l'enveloppe applicative C1c n'est pas sur cette branche → conteneur = isolation en démo.)*
- **P2 — ComfyUI conteneurisé** *(image CPU faite + smoke-test ok, 2026-06-19)* :
  `Dockerfile.comfyui` (python:3.14-slim, ComfyUI épinglé `f2270f0`, torch via build-arg
  `TORCH_CHANNEL`), service `comfyui`, modèles en volume **RO**, sortie sur volume **partagé**
  avec le backend. **Zéro custom node** (le workflow `cinematic_scene_v1` n'utilise que des
  nœuds cœur). Wheels `cp314` présentes sur cpu **ET** cu128 + tout le requirements ComfyUI →
  Python 3.14 conservé (pas de 3.12). ⚠️ ComfyUI exige `--cpu` sans GPU (variable
  `COMFYUI_EXTRA_ARGS`, vidée par l'overlay GPU). E2E RealVisXL = avec le GPU (P3).
- **P3 — Overlay GPU** *(fait + validé 2026-06-19)* : `docker-compose.gpu.yml` (canal cu128 +
  réservation NVIDIA + retrait de `--cpu`). Validé RTX 3060 : `cuda.is_available()=True`,
  génération RealVisXL dans le conteneur.
- **P4 — Validation cross-platform** *(smoke Linux GPU fait)* : stack complète `up`
  (backend+comfyui+ollama **healthy**) + **round-trip backend→comfyui validé** (image
  RealVisXL renvoyée par l'API en ~69 s à froid). Reste : rejeu CPU, procédure Windows/WSL2.
- **P5 — DX & docs** : entrée une-commande, script de fetch modèles, doc des 4 tiers.

## Lancer la stack

**Une commande** (fait tout : config SearXNG, téléchargement des modèles, build, up) :
```bash
cd core
make demo-gpu     # GPU NVIDIA (Linux natif, ou Windows + Docker Desktop/WSL2) — démo pleine
make demo         # CPU seul — tourne partout, lent pour l'image
```
`make demo` télécharge RealVisXL + 4x-UltraSharp (~6,6 Go, HuggingFace) si absents, puis
monte la stack. Backend sur `http://127.0.0.1:8000`. `make down` arrête, `make logs` suit.

Équivalent manuel (sous le capot) :
```bash
cp searxng/settings.example.yml searxng/settings.yml   # config SearXNG (requis)
bash scripts/fetch-models.sh                            # modèles -> ./models (idempotent)
docker compose -f docker-compose.app.yml -f docker-compose.gpu.yml up --build
curl -s http://127.0.0.1:8000/health                   # -> {"status":"ok"}
```
Surcharges (ex. réutiliser des modèles déjà présents) : `cp env.docker.example .env`
puis ajuster `COMFYUI_MODELS_DIR` / `COMFYUI_CHECKPOINT_NAME` (compose charge `.env`).

Générer une image bout en bout (API compatible OpenAI, backend → ComfyUI) :
```bash
curl -sN http://127.0.0.1:8000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"assistant-core-image","messages":[{"role":"user",
       "content":"a cinematic photo of a red fox in a misty forest at golden hour"}]}'
```
Le backend force `image_generation`, appelle ComfyUI (`comfyui:8188`), récupère l'image
via `/view` et la renvoie en data-URI. Validé RTX 3060 : ~45 s à froid (chargement
RealVisXL + draft 30 pas).

Chaque génération range image **et** `manifest.json` (traçabilité : route des étapes
horodatée, paramètres, bloc `runtime`/OS) dans `core/outputs/comfyui/<request_id>/` sur
l'hôte (bind mount). Les fichiers appartiennent à l'utilisateur hôte (`AAC_UID:AAC_GID`,
défaut 1000 ; le backend root les `chown`).

## Risques connus
- **Taille des modèles** = la vraie friction « ça marche tout de suite » (démo pleine).
- **PyTorch sur Python 3.14** — **levé (2026-06-19)** : wheels `cp314` sur cpu (`torch 2.12.1`)
  ET cu128 (`torch 2.11.0`), et tout le requirements ComfyUI a une wheel cp314 → aucune compilation.
- **Prérequis GPU Windows** : Docker Desktop + backend WSL2 + driver NVIDIA (sinon CPU).
- **bwrap imbriqué** = privilèges conteneur (sinon `auto`/`off` en démo).
- **SearXNG** : nécessite `core/searxng/settings.yml` (gitignoré car secret) — `cp` depuis
  `settings.example.yml`. Le template active `format: json` (requis par le backend) et
  `limiter: false` (accès interne). Sans le fichier, le service boucle (exit 127).
