#!/usr/bin/env bash
# fetch-ollama-models.sh — télécharge les modèles LLM dont le routeur AAC a besoin.
# Portable (bash) : Linux natif, ou Windows via WSL2 / Git-bash. Idempotent : saute
# un modèle déjà présent.
#
# Source de vérité des noms = core/app/engine/task_routing.py :
#   qwen3:8b          chat / router / explication / critique / architecture
#   qwen2.5-coder:7b  build code + génération bpy (pipeline Blender)
#   qwen2.5vl:3b      vision (VLM)
#
# Deux modes (auto-détectés, surchargeables via AAC_OLLAMA_MODE=native|docker) :
#   native  -> binaire `ollama` installé sur l'hôte (https://ollama.com/download)
#   docker  -> conteneur `ollama` de la stack démo (docker-compose.app.yml)
set -euo pipefail

cd "$(dirname "$0")/../.."   # racine du repo (depuis scripts/linux/)

MODELS=(
  "qwen3:8b"
  "qwen2.5-coder:7b"
  "qwen2.5vl:3b"
)

COMPOSE="docker compose -f docker/docker-compose.app.yml"

# --- Sélection du mode -------------------------------------------------------
mode="${AAC_OLLAMA_MODE:-}"
if [ -z "$mode" ]; then
  if command -v ollama >/dev/null 2>&1; then
    mode="native"
  elif $COMPOSE ps --status running ollama 2>/dev/null | grep -q ollama; then
    mode="docker"
  else
    echo "✗ Aucun Ollama trouvé." >&2
    echo "  - install native : https://ollama.com/download  (puis relancer)" >&2
    echo "  - ou démo Docker : 'make demo' (ou 'make demo-gpu') dans un autre terminal," >&2
    echo "    puis relancer 'make pull-llms'." >&2
    exit 1
  fi
fi

# Lance `ollama <args...>` dans le bon contexte (hôte ou conteneur).
ollama_cmd() {
  if [ "$mode" = "docker" ]; then
    $COMPOSE exec -T ollama ollama "$@"
  else
    ollama "$@"
  fi
}

echo "== Modèles Ollama (mode: ${mode}) =="

# Liste des modèles déjà présents (une fois, pour l'idempotence).
present="$(ollama_cmd list 2>/dev/null || true)"

for m in "${MODELS[@]}"; do
  if printf '%s\n' "$present" | grep -q -- "$m"; then
    echo "✓ déjà présent : $m"
    continue
  fi
  echo "⤓ pull $m ..."
  ollama_cmd pull "$m"
  echo "✓ $m"
done

echo "== OK. Modèles LLM prêts (mode: ${mode}). =="
