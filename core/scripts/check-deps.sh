#!/usr/bin/env bash
# check-deps.sh — preflight des dépendances AAC (« doctor »). Ne télécharge / n'installe
# rien : détecte ce qui manque et donne la commande pour le corriger. Portable (bash) :
# Linux natif, Windows via WSL2 / Git-bash.
#
#   ✓ = présent / prêt    ✗ = manquant (bloquant)    ~ = optionnel / dégradé
#
# Sortie : 0 si toutes les dépendances BLOQUANTES sont OK, 1 sinon.
set -uo pipefail

cd "$(dirname "$0")/.."   # core/

MODELS=( "qwen3:8b" "qwen2.5-coder:7b" "qwen2.5vl:3b" )
MODELS_DIR="${COMFYUI_MODELS_DIR:-./models}"
COMPOSE="docker compose -f docker-compose.app.yml"

fail=0
ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; }
miss() { printf '  \033[31m✗\033[0m %s\n' "$1"; fail=1; }
warn() { printf '  \033[33m~\033[0m %s\n' "$1"; }
hint() { printf '      → %s\n' "$1"; }

echo "== AAC — preflight des dépendances =="

# --- 1. Docker ---------------------------------------------------------------
echo "Docker"
if command -v docker >/dev/null 2>&1; then
  if docker compose version >/dev/null 2>&1; then
    ok "docker + docker compose"
  else
    miss "docker compose (plugin v2) absent"
    hint "https://docs.docker.com/compose/install/"
  fi
else
  warn "docker absent (requis pour 'make demo' ; inutile en install 100% native)"
  hint "https://docs.docker.com/engine/install/"
fi

# --- 2. Ollama + modèles LLM -------------------------------------------------
echo "Ollama (LLM)"
ollama_list=""
if command -v ollama >/dev/null 2>&1; then
  ollama_list="$(ollama list 2>/dev/null || true)"
  ok "binaire ollama (natif)"
elif $COMPOSE ps --status running ollama 2>/dev/null | grep -q ollama; then
  ollama_list="$($COMPOSE exec -T ollama ollama list 2>/dev/null || true)"
  ok "conteneur ollama (démo Docker)"
else
  miss "aucun Ollama (ni natif, ni conteneur démo en cours)"
  hint "install : https://ollama.com/download   ou   'make demo'"
fi
if command -v ollama >/dev/null 2>&1 && [ -z "$ollama_list" ]; then
  warn "binaire ollama présent mais 'ollama list' vide — serveur arrêté ?"
  hint "démarrer le service ollama, puis relancer 'make doctor'"
elif [ -n "$ollama_list" ]; then
  for m in "${MODELS[@]}"; do
    if printf '%s\n' "$ollama_list" | grep -q -- "$m"; then
      ok "modèle $m"
    else
      miss "modèle $m manquant"
      hint "make pull-llms"
    fi
  done
fi

# --- 3. Modèles ComfyUI (image) ----------------------------------------------
echo "ComfyUI (image)"
ckpt="${COMFYUI_CHECKPOINT_NAME:-RealVisXL_V5.0_fp16.safetensors}"
ups="${COMFYUI_UPSCALE_MODEL_NAME:-4x-UltraSharp.pth}"
if [ -f "${MODELS_DIR}/checkpoints/${ckpt}" ]; then ok "checkpoint ${ckpt}"
else miss "checkpoint ${ckpt} absent"; hint "make fetch-models"; fi
if [ -f "${MODELS_DIR}/upscale_models/${ups}" ]; then ok "upscaler ${ups}"
else miss "upscaler ${ups} absent"; hint "make fetch-models"; fi

# --- 4. Blender (3D, hôte) ---------------------------------------------------
echo "Blender (3D, optionnel)"
blender_bin="${BLENDER_EXE:-${BLENDER_BIN:-}}"
[ -z "$blender_bin" ] && command -v blender >/dev/null 2>&1 && blender_bin="blender"
if [ -n "$blender_bin" ] && "$blender_bin" --version >/dev/null 2>&1; then
  ok "$("$blender_bin" --version 2>/dev/null | head -1)"
else
  warn "Blender introuvable (pipeline 3D désactivé ; le cœur router/planner/executor marche sans)"
  hint "installer Blender puis exporter BLENDER_EXE=/chemin/vers/blender — voir docs/DEPENDENCIES.md"
fi

# --- 5. SearXNG (web) --------------------------------------------------------
echo "SearXNG (web)"
if [ -f searxng/settings.yml ]; then ok "searxng/settings.yml"
else warn "searxng/settings.yml absent (généré par 'make setup')"; hint "make setup"; fi

echo
if [ "$fail" -eq 0 ]; then
  echo "== Preflight OK : toutes les dépendances bloquantes sont prêtes. =="
else
  echo "== Preflight INCOMPLET : voir les ✗ ci-dessus. =="
fi
exit "$fail"
