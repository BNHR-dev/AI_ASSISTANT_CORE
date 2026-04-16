# Infra — AI_ASSISTANT_CORE

Ce dossier décrit la couche runtime de AI_ASSISTANT_CORE dans sa topologie réelle :

- host Windows
- VM Hyper-V
- réseau privé d’échange
- services gardés sur le host quand c’est pertinent
- services canonisés dans la VM

## Rôle

Ce dossier ne contient pas la VM elle-même.
Il contient une représentation versionnée, lisible et partageable de la couche runtime du produit.

## Principe

La VM fait partie du produit final.
Le host reste l’espace principal de travail.
La VM sert de runtime isolé principal et de couche de sécurité structurelle.

## Contenu

- `vm/systemd/` : exemples d’unités systemd côté VM
- `vm/env_examples/` : exemples de variables d’environnement côté VM
- `vm/network/` : topologie réseau et conventions host/VM
- `vm/runbooks/` : notes runtime VM
- `host/powershell/` : scripts PowerShell d’exemple côté host
- `host/firewall/` : notes firewall host
- `searxng/` : exemple de config SearXNG

## Sécurité

Les fichiers ici sont sanitisés.
Ils ne doivent pas contenir :
- secrets réels
- mots de passe
- clés API
- exports sensibles
- snapshots live