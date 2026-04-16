# Firewall host — notes

Le firewall host doit rester :

- minimal
- borné à la VM
- limité aux ports utiles
- cohérent avec la topologie runtime réelle

## Principe

Ne pas ouvrir plus large que nécessaire.

## Ports typiques utiles

- bridge Ollama côté VM via `12001`
- ComfyUI côté host via `8188`

## Rappel

Le runtime principal vit dans la VM.
Le host fournit certaines capacités au runtime VM.