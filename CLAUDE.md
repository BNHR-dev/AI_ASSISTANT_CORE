# AI_ASSISTANT_CORE — règles projet pour Claude Code

## Identité du projet

AI_ASSISTANT_CORE est un orchestrateur IA local orienté création numérique et exécution structurée.

Le noyau du produit est :
- un routeur
- un planner
- un executor
- une surface d’observabilité

Le système prend une demande, produit une décision structurée, construit un plan d’exécution, exécute ce plan étape par étape, puis retourne une sortie utile et traçable.

## Invariants

- Ne pas repartir de zéro
- Ne pas proposer de refonte architecture globale
- Ne pas casser le noyau `routeur + planner + executor`
- Travailler à partir de l’état réel validé
- Privilégier les phases courtes, concrètes, rentables et réversibles
- Préserver la lisibilité, la robustesse et le déterminisme
- Éviter la complexité prématurée

## Architecture

Trajectoire actuelle : Linux/Fedora natif (migration clôturée le 2026-05-30).

- le host Fedora KDE est l'espace principal de travail ET le runtime principal
- les services (Ollama, SearXNG, Open-WebUI) tournent en Docker via `docker-compose.linux.yml`, ports liés à 127.0.0.1
- Blender s'exécute en headless directement sur le host (GPU NVIDIA RTX 3060 12 Go)
- l'ancien contexte host Windows + VM Ubuntu est legacy/archivé (`infra/vm/`, `docker-compose.yml` Windows, scripts `.ps1`/`.bat`) — ne pas le traiter comme l'architecture courante
- l'isolation de l'exécution du code généré reste un objectif produit (cf. audit 2026-06-10, finding C1) ; elle n'est plus portée par une VM aujourd'hui

## Source de vérité

Quand une information diverge, l’ordre de confiance est :

1. `core/app/*`
2. `core/openai_compat.py`
3. la documentation canonique du projet
4. la config runtime réellement utilisée

Ne traite pas les fichiers legacy racine comme source métier si `app/*` dit autre chose.

## Pipeline Blender

Le pipeline Blender est expérimental mais fonctionnel. Il ne modifie pas les invariants du noyau routeur + planner + executor.

- client canonique : `core/app/clients/blender_client.py`
- sorties validées : `scene.py`, `scene.blend`, `preview.png` sous `outputs/blender/<uuid>/`
- `scene.blend` est l'artefact canonique
- `preview.png` est best-effort et non bloquant
- le rendu preview est produit dans un subprocess séparé, distinct du script principal

## Règles de travail

- Distinguer clairement code, doc, runtime déclaré, sécurité déclarée et legacy (Windows/VM)
- Ne pas prétendre vérifier l'état live du firewall, de systemd ou des services si ce n'est pas visible dans le repo ou via une commande exécutée
- Signaler explicitement les ambiguïtés
- Préférer les patchs courts et réversibles
- Pour les tâches multi-fichiers ou ambiguës, commencer par un plan