#!/usr/bin/env bash
# run.sh — AAC en UNE commande (Linux / WSL2 / macOS). Chemin SÉCURISÉ par défaut.
#
# Démarre la stack Docker complète (backend + ollama + searxng + comfyui) AVEC l'overlay
# sandbox durci, auto-détecte le GPU NVIDIA, prépare SearXNG, télécharge les modèles
# (idempotent), puis VÉRIFIE LA SANTÉ RÉELLE de chaque service (échec franc — pas de
# « ready » menteur) avant d'ouvrir la Console.
#
# Unique prérequis : Docker + plugin `compose` v2. (Windows : Docker Desktop + WSL2.)
#
#   ./run.sh              démarre (build au besoin), ouvre /console
#   ./run.sh --down       arrête la stack
#   ./run.sh --logs       suit les logs
#   ./run.sh --no-open    ne pas ouvrir le navigateur
#   ./run.sh --no-models  ne pas (télé)charger les modèles
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOCKER_DIR="$REPO_ROOT/docker"
export COMFYUI_MODELS_DIR="${COMFYUI_MODELS_DIR:-$DOCKER_DIR/models}"

OPEN=1; MODELS=1; ACTION=up
for a in "$@"; do
  case "$a" in
    --down) ACTION=down ;;
    --logs) ACTION=logs ;;
    --no-open) OPEN=0 ;;
    --no-models) MODELS=0 ;;
    -h|--help) sed -n '2,17p' "$0"; exit 0 ;;
    *) echo "option inconnue : $a" >&2; exit 2 ;;
  esac
done

die() { printf '\033[31mERREUR:\033[0m %s\n' "$*" >&2; exit 1; }
log() { printf '\n\033[36m== %s ==\033[0m\n' "$*"; }

command -v docker >/dev/null 2>&1 || die "Docker absent. Installez Docker (+ compose v2). Windows : Docker Desktop + WSL2."
docker compose version >/dev/null 2>&1 || die "plugin 'docker compose' v2 absent."
docker info >/dev/null 2>&1 || die "le daemon Docker ne répond pas (démarrez Docker / Docker Desktop)."

# Compose : base + sandbox DURCI (sécurité) TOUJOURS ; overlay GPU si NVIDIA réellement exposé.
COMPOSE=(docker compose --project-directory "$DOCKER_DIR"
         -f "$DOCKER_DIR/docker-compose.app.yml"
         -f "$DOCKER_DIR/docker-compose.sandbox.yml")
GPU=0
if command -v nvidia-smi >/dev/null 2>&1 && docker info 2>/dev/null | grep -qi nvidia; then
  GPU=1
  COMPOSE+=(-f "$DOCKER_DIR/docker-compose.gpu.yml")
fi

case "$ACTION" in
  down) log "Arrêt de la stack"; "${COMPOSE[@]}" down; exit 0 ;;
  logs) "${COMPOSE[@]}" logs -f; exit 0 ;;
esac

log "GPU NVIDIA : $([ "$GPU" = 1 ] && echo 'détecté (CUDA)' || echo 'non détecté (CPU)')"

# SearXNG : settings.yml depuis l'exemple (format json activé -> évite le 403 du backend).
if [ ! -f "$DOCKER_DIR/searxng/settings.yml" ]; then
  cp "$DOCKER_DIR/searxng/settings.example.yml" "$DOCKER_DIR/searxng/settings.yml"
  log "SearXNG : settings.yml créé depuis l'exemple (json activé)"
fi

# Modèles image AVANT le up (montés en lecture seule dans comfyui).
if [ "$MODELS" = 1 ]; then
  log "Modèles image (ComfyUI) -> $COMFYUI_MODELS_DIR"
  bash "$REPO_ROOT/scripts/linux/fetch-models.sh"
fi

log "Build + démarrage de la stack"
"${COMPOSE[@]}" up -d --build

# Modèles LLM dans le conteneur ollama (idempotent).
if [ "$MODELS" = 1 ]; then
  log "Modèles LLM (Ollama, dans le conteneur)"
  AAC_OLLAMA_MODE=docker bash "$REPO_ROOT/scripts/linux/fetch-ollama-models.sh" || die "échec du pull des modèles LLM"
fi

# --- Gate de santé RÉELLE (pas de « ready » menteur) ---
log "Vérification de la santé réelle des services"
health() { local id="$1"; [ -n "$id" ] && docker inspect --format '{{.State.Health.Status}}' "$id" 2>/dev/null || echo absent; }
cid_backend="$("${COMPOSE[@]}" ps -q aac-backend 2>/dev/null || true)"
cid_comfy="$("${COMPOSE[@]}" ps -q comfyui 2>/dev/null || true)"
deadline=$(( SECONDS + 240 )); back=0; cf=0
while [ "$SECONDS" -lt "$deadline" ]; do
  [ "$(health "$cid_backend")" = healthy ] && back=1
  [ "$(health "$cid_comfy")" = healthy ] && cf=1
  [ "$back" = 1 ] && [ "$cf" = 1 ] && break
  sleep 4
done
[ "$back" = 1 ] || die "backend pas 'healthy' (diagnostic : ./run.sh --logs)"
[ "$cf" = 1 ]   || die "comfyui pas 'healthy' (diagnostic : ./run.sh --logs)"

# Ollama réellement peuplé (le « ready » d'un ollama vide est le mensonge classique).
present="$("${COMPOSE[@]}" exec -T ollama ollama list 2>/dev/null || true)"
for m in qwen3:8b qwen2.5-coder:7b qwen2.5vl:3b; do
  printf '%s\n' "$present" | grep -q "$m" || die "Ollama répond mais le modèle '$m' manque (relancez sans --no-models)."
done

log "OK — stack saine. Console : http://127.0.0.1:8000/console"
if [ "$OPEN" = 1 ]; then
  ( xdg-open http://127.0.0.1:8000/console >/dev/null 2>&1 \
    || open    http://127.0.0.1:8000/console >/dev/null 2>&1 \
    || true ) &
fi
