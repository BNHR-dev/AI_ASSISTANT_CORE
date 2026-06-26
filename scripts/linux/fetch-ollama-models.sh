#!/usr/bin/env bash
# fetch-ollama-models.sh -- pull the LLM models the AAC router needs.
# Portable (bash): native Linux, or Windows via WSL2 / Git-bash. Idempotent: skips a
# model already present.
#
# Source of truth for the names = scripts/models.manifest (kind=ollama). Kept in sync with
# core/app/engine/task_routing.py:
#   qwen3:8b          chat / router / explanation / critique / architecture
#   qwen2.5-coder:7b  code build + bpy generation (Blender pipeline)
#   qwen2.5vl:3b      vision (VLM)
#
# Two modes (auto-detected, override via AAC_OLLAMA_MODE=native|docker):
#   native  -> `ollama` binary installed on the host (https://ollama.com/download)
#   docker  -> the demo stack `ollama` container (docker-compose.app.yml)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"
MANIFEST="${AAC_MODELS_MANIFEST:-$REPO_ROOT/scripts/models.manifest}"

[ -f "$MANIFEST" ] || { echo "manifest introuvable : $MANIFEST" >&2; exit 1; }

trim() { echo "$1" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//'; }

MODELS=()
while IFS='|' read -r kind subdir name size url || [ -n "$kind" ]; do
  case "$kind" in ''|'#'*) continue ;; esac
  [ "$(trim "$kind")" = "ollama" ] || continue
  MODELS+=("$(trim "$name")")
done < "$MANIFEST"

[ "${#MODELS[@]}" -gt 0 ] || { echo "aucun modele ollama dans le manifest : $MANIFEST" >&2; exit 1; }

COMPOSE="docker compose -f docker/docker-compose.app.yml"

# --- Mode selection ----------------------------------------------------------
mode="${AAC_OLLAMA_MODE:-}"
if [ -z "$mode" ]; then
  if command -v ollama >/dev/null 2>&1; then
    mode="native"
  elif $COMPOSE ps --status running ollama 2>/dev/null | grep -q ollama; then
    mode="docker"
  else
    echo "[!] Aucun Ollama trouve." >&2
    echo "  - install native : https://ollama.com/download  (puis relancer)" >&2
    echo "  - ou demo Docker : 'make demo' (ou 'make demo-gpu') dans un autre terminal," >&2
    echo "    puis relancer 'make pull-llms'." >&2
    exit 1
  fi
fi

# Run `ollama <args...>` in the right context (host or container).
ollama_cmd() {
  if [ "$mode" = "docker" ]; then
    $COMPOSE exec -T ollama ollama "$@"
  else
    ollama "$@"
  fi
}

echo "== Modeles Ollama (mode: ${mode}) =="

# Models already present (once, for idempotence).
present="$(ollama_cmd list 2>/dev/null || true)"

for m in "${MODELS[@]}"; do
  if printf '%s\n' "$present" | grep -q -- "$m"; then
    echo "[OK] deja present : $m"
    continue
  fi
  echo "[DL] pull $m ..."
  ollama_cmd pull "$m"
  echo "[OK] $m"
done

echo "== OK. Modeles LLM prets (mode: ${mode}). =="
