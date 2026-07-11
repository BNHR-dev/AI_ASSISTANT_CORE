#!/usr/bin/env bash
# live-tests.sh — lance le tier de tests LIVE contre la stack Docker qui tourne.
#
# Le tier live (core/tests/test_live_stack.py) exécute les vrais pipelines
# (Ollama, ComfyUI/GPU, Blender local) puis rejoue les runs produits. Il est
# gaté par AAC_LIVE_TESTS=1 : ce script résout les IPs des conteneurs, pose
# l'environnement et lance pytest. Linux natif uniquement (les IPs du réseau
# Docker ne sont pas routables depuis l'hôte sous Docker Desktop mac/Windows).
#
# Prérequis :
#   - stack démarrée : ./run.sh
#   - un venv Python avec core/requirements-dev.txt installé, actif OU
#     désigné par $PYTHON (défaut : `python3`)
#
#   ./scripts/linux/live-tests.sh            # tout le tier live
#   ./scripts/linux/live-tests.sh -k comfyui # filtre pytest habituel
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PYTHON="${PYTHON:-python3}"

die() { echo "ERREUR : $*" >&2; exit 1; }

command -v docker >/dev/null || die "docker introuvable"
"$PYTHON" -c "import pytest" 2>/dev/null \
  || die "pytest absent — installer core/requirements-dev.txt dans le venv courant (ou poser PYTHON=...)"

container_ip() {
  docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$1" 2>/dev/null
}

OLLAMA_IP="$(container_ip aac-ollama-1)"
COMFY_IP="$(container_ip aac-comfyui-1)"
[ -n "$OLLAMA_IP" ] || die "conteneur aac-ollama-1 introuvable — stack démarrée ? (./run.sh)"
[ -n "$COMFY_IP" ] || die "conteneur aac-comfyui-1 introuvable — stack démarrée ? (./run.sh)"

export AAC_LIVE_TESTS=1
export OLLAMA_BASE_URL="http://$OLLAMA_IP:11434"
export OLLAMA_URL="http://$OLLAMA_IP:11434/api/generate"
export OLLAMA_TAGS_URL="http://$OLLAMA_IP:11434/api/tags"
export COMFYUI_URL="http://$COMFY_IP:8188"
export COMFYUI_AUTO_START=0
# Chemins HÔTE des volumes partagés : les images naissent côté conteneur,
# le test les lit ici. Les modèles permettent le hash sha256 (bloc repro).
export COMFYUI_OUTPUT_DIR="${COMFYUI_OUTPUT_DIR:-$REPO_ROOT/docker/outputs/comfyui}"
export COMFYUI_MODELS_DIR="${COMFYUI_MODELS_DIR:-$REPO_ROOT/docker/models}"
export COMFYUI_CHECKPOINT_NAME="${COMFYUI_CHECKPOINT_NAME:-RealVisXL_V5.0_fp16.safetensors}"

echo "== Tier LIVE : ollama=$OLLAMA_IP comfyui=$COMFY_IP =="
cd "$REPO_ROOT/core"
exec "$PYTHON" -m pytest -m live -v "$@"
