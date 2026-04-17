# VM spec — AI_ASSISTANT_CORE

## Rôle de ce fichier

Spec déclarative unique de la VM Hyper-V qui porte le runtime principal du produit.

Ce fichier n'est pas une pipeline.
Ce fichier ne provisionne rien.
Ce fichier ne prétend pas refléter l'état live de la VM.

C'est une source de vérité figée : si la VM doit être reconstruite, les valeurs ci-dessous doivent être atteintes à l'identique pour que le runtime canonique reste cohérent avec le reste du repo.

Les valeurs marquées `<à remplir>` ne sont pas observables depuis le repo. Elles doivent être renseignées par l'opérateur à partir de l'état réel de la VM, puis figées ici.

## Identité VM

| Champ | Valeur |
|---|---|
| Nom Hyper-V | `<à remplir>` |
| Génération Hyper-V | `<à remplir : Gen1 ou Gen2>` |
| vCPU | `<à remplir>` |
| RAM allouée | `<à remplir>` |
| Disque système (taille) | `<à remplir>` |
| Disque système (format) | `<à remplir : VHDX dynamique ou fixe>` |
| Secure Boot | `<à remplir : activé ou désactivé>` |
| Checkpoints automatiques | recommandé désactivé sur une VM runtime |

## OS invité

| Champ | Valeur |
|---|---|
| Distribution | Linux (déduit de `/home/bnhr`, `/usr/bin/python3`, systemd, docker) |
| Distribution précise | `<à remplir : ex. Ubuntu Server 24.04 LTS>` |
| Noyau | `<à remplir>` |
| Utilisateur runtime | `bnhr` (voir [`systemd/aicore-backend.service.example`](systemd/aicore-backend.service.example)) |
| Working directory runtime | `/home/bnhr/aicore/projects/core` |

## Réseau

Source de vérité : [`network/topology.md`](network/topology.md).

| Champ | Valeur |
|---|---|
| Switch virtuel Hyper-V | `AICORE-INT` (réseau privé) |
| IP host | `192.168.77.1` |
| IP VM | `192.168.77.10` |
| Résolution DNS sortante | `<à remplir : DNS utilisé par la VM>` |

### Flux runtime canoniques

- VM → Ollama host : `http://192.168.77.1:12001`
  (repose sur un `portproxy` Windows host `192.168.77.1:12001 -> 127.0.0.1:12000`)
- VM → ComfyUI host : `http://192.168.77.1:8188`

### Invariant réseau

Le chemin direct VM → `192.168.77.1:12000` ne doit pas être documenté ni utilisé comme chemin runtime validé.

## Services runtime dans la VM

Source de vérité : [`runbooks/vm_runtime_notes.md`](runbooks/vm_runtime_notes.md) + unités systemd d'exemple.

### Backend FastAPI

- Unité systemd : `aicore-backend.service` (gabarit : [`systemd/aicore-backend.service.example`](systemd/aicore-backend.service.example))
- Bind : `127.0.0.1:8000`
- Commande lancée : `uvicorn app.main:app --host 127.0.0.1 --port 8000`
- Working directory : `/home/bnhr/aicore/projects/core`
- Fichier d'env : `/home/bnhr/aicore/projects/core/.env`
- Restart : `always`, `RestartSec=3`

### SearXNG

- Unité systemd : `aicore-searxng.service` (gabarit : [`systemd/aicore-searxng.service.example`](systemd/aicore-searxng.service.example))
- Bind : `127.0.0.1:8081`
- Mode : `Type=oneshot` + `RemainAfterExit=yes`, lancé via `docker compose up -d searxng`
- Dépend de `docker.service`

## Dépendances système dans la VM

| Composant | Contrainte | Version figée |
|---|---|---|
| Python | invoqué via `/usr/bin/python3` | `<à remplir : version majeure.mineure figée>` |
| uvicorn | requis par le backend | `<à remplir>` |
| docker engine | requis par le service SearXNG | `<à remplir>` |
| docker compose | `docker compose` (v2, plugin) | `<à remplir>` |
| systemd | runtime d'unités | version système de la distro retenue |

Le code applicatif (FastAPI, routeur, planner, executor) est versionné dans `core/app/*` — ce n'est pas une dépendance de VM, c'est du contenu déployé dans `/home/bnhr/aicore/projects/core`.

## Variables d'environnement

Le gabarit officiel est [`env_examples/.env.vm.example`](env_examples/.env.vm.example).

Toute divergence entre le `.env` réel de la VM et ce gabarit doit être intentionnelle et documentée. Les valeurs sensibles (clés, secrets) ne figurent jamais ici ni dans le gabarit.

## Surface d'observabilité côté VM

Les vérifications minimales attendues sur la VM reconstruite :

- `sudo systemctl status aicore-backend --no-pager` → actif
- `sudo systemctl status aicore-searxng --no-pager` → actif
- `curl -s http://127.0.0.1:8000/health` → `status=ok`
- `curl -s http://127.0.0.1:8000/health/runtime` → endpoint joignable et cohérent avec la topologie attendue (`ollama`, `searxng`, `comfyui`)

Si `/health/runtime` signale `ollama.ready=false`, le problème peut venir du host (service Ollama, `portproxy`, firewall) ou d’un mauvais raccord de configuration côté VM. Ce fichier ne tranche pas l’état live ; il fixe seulement la cible déclarative attendue.

## Ce qui n'est PAS dans ce fichier, volontairement

- aucune commande de provisioning Hyper-V
- aucune étape d'installation Linux
- aucun script d'installation de paquets
- aucun snapshot ou export de VM
- aucun secret, aucune clé, aucun hash
- aucune prétention de vérifier l'état live de la VM

Ce fichier reste déclaratif. Pour automatiser la reconstruction, il faudra un move ultérieur explicite (pipeline dédiée), pas une dérive de ce fichier.

## Règle de mise à jour

Ce fichier est mis à jour uniquement quand :

1. une valeur `<à remplir>` devient connue et figée ;
2. un flux réseau canonique change (et alors `network/topology.md` change en même temps) ;
3. un service runtime canonique est ajouté, supprimé ou renommé (et alors l'unité systemd correspondante change en même temps) ;
4. une dépendance système est explicitement contrainte à une version minimale.

Il ne doit pas dériver pour refléter des expérimentations, des états intermédiaires ou des chemins non validés.
