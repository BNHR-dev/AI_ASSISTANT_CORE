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

La VM fait partie du produit final.

- le host Windows reste l’espace principal de travail
- la VM est le runtime isolé principal
- la VM est une brique architecture / sécurité du produit
- elle n’est pas un simple environnement de dev temporaire

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

- Distinguer clairement code, doc, runtime déclaré host/VM, sécurité déclarée et legacy
- Ne pas prétendre vérifier l'état live de la VM, du firewall ou de systemd si ce n'est pas visible dans le repo
- Signaler explicitement les ambiguïtés
- Préférer les patchs courts et réversibles
- Pour les tâches multi-fichiers ou ambiguës, commencer par un plan