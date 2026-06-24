#!/usr/bin/env bash
# fetch-models.sh -- download the AAC demo image models (P5).
# Portable (bash + curl): Linux, macOS, Windows/WSL2. Public HuggingFace models
# (no token). Model names/URLs/sizes come from scripts/models.manifest -- the single
# source of truth shared with the Windows script (scripts/windows/Fetch-ComfyUIModels.ps1).
#
# Target: $COMFYUI_MODELS_DIR (default docker/models, relative to the repo root).
# Idempotent: skips a file already present with the expected size.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
MANIFEST="${AAC_MODELS_MANIFEST:-$REPO_ROOT/scripts/models.manifest}"
MODELS_DIR="${COMFYUI_MODELS_DIR:-$REPO_ROOT/docker/models}"

[ -f "$MANIFEST" ] || { echo "manifest introuvable : $MANIFEST" >&2; exit 1; }

filesize() { stat -c%s "$1" 2>/dev/null || stat -f%z "$1" 2>/dev/null || echo 0; }
trim() { echo "$1" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//'; }

fetch() {
  local subdir=$1 filename=$2 expected=$3 url=$4
  local dir="${MODELS_DIR}/${subdir}" dest
  dest="${dir}/${filename}"
  mkdir -p "$dir"

  if [ -f "$dest" ] && [ "$(filesize "$dest")" = "$expected" ]; then
    echo "[OK] deja present : ${subdir}/${filename}"
    return 0
  fi

  echo "[DL] ${subdir}/${filename}  (~$(( expected / 1024 / 1024 )) Mo)"
  curl -fL --retry 3 --retry-delay 2 -# -o "${dest}.part" "$url"

  local got
  got=$(filesize "${dest}.part")
  if [ "$got" != "$expected" ]; then
    echo "[!] ECHEC ${filename} : $got octets (attendu $expected)" >&2
    rm -f "${dest}.part"
    exit 1
  fi
  mv "${dest}.part" "$dest"
  echo "[OK] ${subdir}/${filename}"
}

echo "== Telechargement des modeles image -> ${MODELS_DIR}/ =="
while IFS='|' read -r kind subdir name size url || [ -n "$kind" ]; do
  case "$kind" in ''|'#'*) continue ;; esac
  [ "$(trim "$kind")" = "comfyui" ] || continue
  fetch "$(trim "$subdir")" "$(trim "$name")" "$(trim "$size")" "$(trim "$url")"
done < "$MANIFEST"
echo "== OK. Modeles image prets dans ${MODELS_DIR}/ =="
