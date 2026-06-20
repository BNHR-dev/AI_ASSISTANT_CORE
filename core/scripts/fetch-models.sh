#!/usr/bin/env bash
# fetch-models.sh — télécharge les modèles de la démo AAC (P5).
# Portable (bash + curl) : Linux, macOS, Windows/WSL2. Modèles publics HuggingFace
# (aucun token requis).
#
#   RealVisXL V5.0 fp16 (checkpoint SDXL, VAE intégrée)  -> checkpoints/    (~6,5 Go)
#   4x-UltraSharp       (upscaler ESRGAN)                -> upscale_models/ (~64 Mo)
#
# Cible : $COMFYUI_MODELS_DIR (défaut ./models, relatif à core/).
# Idempotent : saute un fichier déjà présent et de la bonne taille.
set -euo pipefail

MODELS_DIR="${COMFYUI_MODELS_DIR:-./models}"

filesize() { stat -c%s "$1" 2>/dev/null || stat -f%z "$1" 2>/dev/null || echo 0; }

fetch() {
  local subdir=$1 filename=$2 url=$3 expected=$4
  local dir="${MODELS_DIR}/${subdir}" dest
  dest="${dir}/${filename}"
  mkdir -p "$dir"

  if [ -f "$dest" ] && [ "$(filesize "$dest")" = "$expected" ]; then
    echo "✓ déjà présent : ${subdir}/${filename}"
    return 0
  fi

  echo "⤓ ${subdir}/${filename}  (~$(( expected / 1024 / 1024 )) Mo)"
  curl -fL --retry 3 --retry-delay 2 -# -o "${dest}.part" "$url"
  mv "${dest}.part" "$dest"

  local got
  got=$(filesize "$dest")
  if [ "$got" != "$expected" ]; then
    echo "✗ ÉCHEC ${filename} : $got octets (attendu $expected)" >&2
    exit 1
  fi
  echo "✓ ${subdir}/${filename}"
}

echo "== Téléchargement des modèles -> ${MODELS_DIR}/ =="
fetch checkpoints    "RealVisXL_V5.0_fp16.safetensors" \
  "https://huggingface.co/SG161222/RealVisXL_V5.0/resolve/main/RealVisXL_V5.0_fp16.safetensors" \
  6938065488
fetch upscale_models "4x-UltraSharp.pth" \
  "https://huggingface.co/Kim2091/UltraSharp/resolve/main/4x-UltraSharp.pth" \
  66961958
echo "== OK. Modèles prêts dans ${MODELS_DIR}/ =="
