# Topologie runtime — AI_ASSISTANT_CORE

## Lecture correcte

Le runtime canonique n’est pas un stack unique host-side.

La topologie validée est :

- host Windows
- VM Hyper-V
- réseau privé d’échange
- backend principal dans la VM
- SearXNG dans la VM
- Ollama sur le host
- ComfyUI sur le host

## Réseau

- réseau privé : `AICORE-INT`
- host : `192.168.77.1`
- VM : `192.168.77.10`

## Flux utiles

### VM → Ollama host
`http://192.168.77.1:12001`

Dans l’état actuel validé, ce flux repose sur un `portproxy` Windows :

`192.168.77.1:12001 -> 127.0.0.1:12000`

### VM → ComfyUI host
`http://192.168.77.1:8188`

## Invariant important

Le chemin direct VM → `192.168.77.1:12000` ne doit pas être documenté comme chemin runtime validé.