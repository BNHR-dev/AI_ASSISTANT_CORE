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
- **P1 — Backend conteneurisé** *(en cours)*
  - **P1a** *(fait)* : `Dockerfile` backend (Python 3.14 + `requirements.txt` + app), `up`
    → `/health` ok, parle à Ollama/SearXNG. CPU.
  - **P1b** *(suivant)* : couche **Blender + bwrap** dans l'image backend.
- **P2 — ComfyUI conteneurisé** : image + volume modèles + workflows draft/final.
  ⚠️ PyTorch/CUDA sur Python 3.14 = canal `cu128`.
- **P3 — Overlay GPU** : `docker-compose.gpu.yml`, validé sur Fedora, documenté Windows/WSL2.
- **P4 — Validation cross-platform** : smoke `up` Linux (GPU + CPU), procédure Windows/WSL2.
- **P5 — DX & docs** : entrée une-commande, script de fetch modèles, doc des 4 tiers.

## Lancer (état P1a)
```bash
cd core
cp env.docker.example .env.docker       # ajuster si besoin
docker compose -f docker-compose.app.yml up --build
curl -s http://127.0.0.1:8000/health    # -> {"status":"ok"}
```

## Risques connus
- **Taille des modèles** = la vraie friction « ça marche tout de suite » (démo pleine).
- **PyTorch/CUDA sur Python 3.14** en conteneur (wheels `cu128`) — traité en P2.
- **Prérequis GPU Windows** : Docker Desktop + backend WSL2 + driver NVIDIA (sinon CPU).
- **bwrap imbriqué** = privilèges conteneur (sinon `auto`/`off` en démo).
