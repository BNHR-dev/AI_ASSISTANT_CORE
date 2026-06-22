#!/usr/bin/env bash
# bootstrap.sh — AAC install natif Linux (machine vierge).
# Idempotent, cross-distro : Fedora/RHEL, Debian/Ubuntu, Arch, openSUSE.
#
# Usage :
#   ./bootstrap.sh                       installation complète (prompt choix dossier)
#   ./bootstrap.sh --data-root /mnt/ai   installation dans /mnt/ai/
#   ./bootstrap.sh --check-only          mode doctor : vérifie sans installer
#   ./bootstrap.sh --skip-comfyui        saute ComfyUI (phase la plus lourde)
#   ./bootstrap.sh --data-root /mnt/ai --skip-comfyui

set -euo pipefail

# =============================================================================
# Paramètres
# =============================================================================
CHECK_ONLY=0
SKIP_COMFYUI=0
DATA_ROOT=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --check-only)   CHECK_ONLY=1 ;;
    --skip-comfyui) SKIP_COMFYUI=1 ;;
    --data-root)    DATA_ROOT="$2"; shift ;;
    *) echo "Option inconnue : $1" >&2; exit 1 ;;
  esac
  shift
done

# =============================================================================
# Chemins du dépôt
# =============================================================================
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# Repo unique : le code Python vit à core/ (plus de sous-module core/core/).
CORE_DIR="$REPO_ROOT/core"

# =============================================================================
# Sortie colorée
# =============================================================================
GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m';  GRAY='\033[0;37m'; RESET='\033[0m'

FAIL=0
step()  { echo -e "\n${CYAN}=== $* ===${RESET}"; }
ok()    { echo -e "  ${GREEN}[OK]  ${RESET} $*"; }
miss()  { echo -e "  ${RED}[X]   ${RESET} $*"; FAIL=1; }
warn()  { echo -e "  ${YELLOW}[~]   ${RESET} $*"; }
hint()  { echo -e "        ${GRAY}-> $*${RESET}"; }

# =============================================================================
# Détection du gestionnaire de paquets
# =============================================================================
detect_pkg_manager() {
  if   command -v dnf     &>/dev/null; then echo dnf
  elif command -v apt-get &>/dev/null; then echo apt
  elif command -v pacman  &>/dev/null; then echo pacman
  elif command -v zypper  &>/dev/null; then echo zypper
  else echo ""; fi
}

PKG=$(detect_pkg_manager)

pkg_install() {
  local pkg_dnf="$1" pkg_apt="$2" pkg_pacman="$3" pkg_zypper="$4"
  case "$PKG" in
    dnf)    sudo dnf install -y "$pkg_dnf" ;;
    apt)    sudo apt-get install -y "$pkg_apt" ;;
    pacman) sudo pacman -S --noconfirm "$pkg_pacman" ;;
    zypper) sudo zypper install -y "$pkg_zypper" ;;
  esac
}

# =============================================================================
# Résolution du dossier de données (DATA_ROOT)
# =============================================================================
if [[ $CHECK_ONLY -eq 0 && -z "$DATA_ROOT" ]]; then
  # Suggérer le point de montage avec le plus d'espace libre hors /
  SUGGESTED=$(df -h --output=target,avail 2>/dev/null \
    | tail -n +2 | grep -v '^/$' | sort -k2 -rh | head -1 | awk '{print $1"/AAC"}' \
    || echo "$HOME/AAC")
  echo -e "\n${CYAN}Dossier racine pour les données IA [$SUGGESTED] :${RESET} \c"
  read -r INPUT
  DATA_ROOT="${INPUT:-$SUGGESTED}"
fi

OLLAMA_MODELS_DIR="${DATA_ROOT:+$DATA_ROOT/ollama/models}"
COMFYUI_DIR="${DATA_ROOT:+$DATA_ROOT/ComfyUI}"
COMFYUI_DIR="${COMFYUI_DIR:-$HOME/AAC/ComfyUI}"

echo -e "${CYAN}AAC — bootstrap Linux${RESET}"
echo -e "${GRAY}Dépôt : $REPO_ROOT${RESET}"
[[ $CHECK_ONLY -eq 1 ]] && echo -e "${YELLOW}(mode CheckOnly : aucune installation)${RESET}"

# =============================================================================
# Phase 1 — paquets système
# =============================================================================
step "Paquets système"

if [[ -z "$PKG" ]]; then
  miss "gestionnaire de paquets non reconnu (testé : dnf, apt, pacman, zypper)"
else
  ok "gestionnaire : $PKG"

  check_or_install() {
    local cmd="$1" label="$2" p_dnf="$3" p_apt="$4" p_pac="$5" p_zyp="$6"
    if command -v "$cmd" &>/dev/null; then
      ok "$label : $(command -v "$cmd")"
    elif [[ $CHECK_ONLY -eq 1 ]]; then
      miss "$label absent"
    else
      echo -e "  ${GRAY}installation de $label...${RESET}"
      pkg_install "$p_dnf" "$p_apt" "$p_pac" "$p_zyp"
      command -v "$cmd" &>/dev/null && ok "$label installé" || miss "échec installation $label"
    fi
  }

  check_or_install git      "Git"       git         git         git         git
  check_or_install python3  "Python 3"  python3     python3     python      python3
  check_or_install 7z       "7-Zip"     p7zip       p7zip-full  p7zip       p7zip
  check_or_install curl     "curl"      curl        curl        curl        curl
  check_or_install docker   "Docker"    docker      docker.io   docker      docker
  # bubblewrap = sandbox OS-level du code bpy généré (pipeline Blender natif).
  # Sans lui, AAC_BLENDER_SANDBOX=auto s'exécute SANS confinement, =require échoue.
  check_or_install bwrap    "Bubblewrap" bubblewrap bubblewrap  bubblewrap  bubblewrap
fi

# =============================================================================
# Phase 2 — Docker : démarrage + SearXNG
# =============================================================================
step "Docker + SearXNG"

if ! command -v docker &>/dev/null; then
  if [[ $CHECK_ONLY -eq 0 ]]; then
    warn "Docker absent — tentative install via get.docker.com"
    curl -fsSL https://get.docker.com | sudo sh
    sudo systemctl enable --now docker
    sudo usermod -aG docker "$USER"
    warn "Groupe docker ajouté — déconnectez-vous/reconnectez-vous pour l'effet"
  else
    miss "Docker absent"
    hint "curl -fsSL https://get.docker.com | sudo sh"
  fi
fi

if command -v docker &>/dev/null; then
  if ! docker info &>/dev/null 2>&1; then
    if [[ $CHECK_ONLY -eq 0 ]]; then
      sudo systemctl start docker 2>/dev/null || true
      sleep 2
    fi
  fi

  if docker info &>/dev/null 2>&1; then
    ok "daemon Docker joignable"
    SEARX_RUNNING=$(docker ps  --filter 'name=searxng' --format '{{.Names}}' 2>/dev/null | grep -c searxng || true)
    SEARX_EXISTS=$(docker  ps -a --filter 'name=searxng' --format '{{.Names}}' 2>/dev/null | grep -c searxng || true)

    if [[ $SEARX_RUNNING -gt 0 ]]; then
      ok "SearXNG en cours d'exécution (http://127.0.0.1:8081/search)"
    elif [[ $CHECK_ONLY -eq 1 ]]; then
      [[ $SEARX_EXISTS -gt 0 ]] && warn "conteneur SearXNG présent mais arrêté" || miss "conteneur SearXNG absent"
      hint "docker run -d --name searxng --restart unless-stopped -p 127.0.0.1:8081:8080 searxng/searxng"
    elif [[ $SEARX_EXISTS -gt 0 ]]; then
      docker start searxng
      ok "SearXNG redémarré (http://127.0.0.1:8081/search)"
    else
      echo -e "  ${GRAY}pull + démarrage de searxng/searxng...${RESET}"
      docker run -d --name searxng --restart unless-stopped \
        -p 127.0.0.1:8081:8080 searxng/searxng
      sleep 3
      docker ps --filter 'name=searxng' --format '{{.Names}}' | grep -q searxng \
        && ok "SearXNG démarré (http://127.0.0.1:8081/search)" \
        || miss "échec démarrage SearXNG"
    fi
  else
    warn "daemon Docker injoignable — relancer après reconnexion si groupe docker vient d'être ajouté"
  fi
fi

# =============================================================================
# Phase 2b — NVIDIA Container Toolkit (GPU dans les conteneurs Docker)
# =============================================================================
# Requis pour le chemin GPU (docker/docker-compose.gpu.yml). Sans lui, un GPU NVIDIA
# présent sur l'hôte n'est PAS visible dans les conteneurs (ollama/comfyui = CPU).
step "NVIDIA Container Toolkit (GPU Docker)"
if ! command -v nvidia-smi &>/dev/null && ! lspci 2>/dev/null | grep -qi nvidia; then
  warn "pas de GPU NVIDIA détecté — toolkit ignoré (la stack tournera en CPU)"
elif command -v nvidia-ctk &>/dev/null; then
  ok "nvidia-container-toolkit déjà présent"
elif [[ $CHECK_ONLY -eq 1 ]]; then
  miss "nvidia-container-toolkit absent (GPU présent mais non exposé aux conteneurs)"
  hint "relancer bootstrap.sh sans --check-only — voir docs/DEPENDENCIES.md"
else
  case "$PKG" in
    dnf)
      curl -fsSL https://nvidia.github.io/libnvidia-container/stable/rpm/nvidia-container-toolkit.repo \
        | sudo tee /etc/yum.repos.d/nvidia-container-toolkit.repo >/dev/null
      pkg_install nvidia-container-toolkit nvidia-container-toolkit nvidia-container-toolkit nvidia-container-toolkit
      ;;
    apt)
      curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
        | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
      curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
        | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
        | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list >/dev/null
      sudo apt-get update
      pkg_install nvidia-container-toolkit nvidia-container-toolkit nvidia-container-toolkit nvidia-container-toolkit
      ;;
    *)
      warn "$PKG : installer nvidia-container-toolkit manuellement, puis: sudo nvidia-ctk runtime configure --runtime=docker"
      ;;
  esac
  if command -v nvidia-ctk &>/dev/null; then
    sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker
    ok "nvidia-container-toolkit installé + runtime Docker configuré"
  else
    warn "nvidia-container-toolkit non posé automatiquement — voir docs/DEPENDENCIES.md"
  fi
fi

# =============================================================================
# Phase 3 — Ollama : install + OLLAMA_MODELS + modèles LLM
# =============================================================================
step "Ollama (LLM)"
LLM_MODELS=(qwen3:8b qwen2.5-coder:7b qwen2.5vl:3b)

if ! command -v ollama &>/dev/null; then
  if [[ $CHECK_ONLY -eq 0 ]]; then
    echo -e "  ${GRAY}installation Ollama (script officiel)...${RESET}"
    curl -fsSL https://ollama.com/install.sh | sh
  else
    miss "ollama absent"
    hint "curl -fsSL https://ollama.com/install.sh | sh"
  fi
fi

if command -v ollama &>/dev/null; then
  ok "binaire ollama : $(command -v ollama)"

  # OLLAMA_MODELS : configurer si DATA_ROOT fourni et variable non définie
  if [[ -n "$OLLAMA_MODELS_DIR" ]]; then
    if [[ -n "${OLLAMA_MODELS:-}" ]]; then
      ok "OLLAMA_MODELS déjà défini → $OLLAMA_MODELS (conservé)"
    elif [[ $CHECK_ONLY -eq 0 ]]; then
      mkdir -p "$OLLAMA_MODELS_DIR"
      # Persistance dans ~/.bashrc et ~/.zshrc si présents
      for rc in "$HOME/.bashrc" "$HOME/.zshrc" "$HOME/.profile"; do
        [[ -f "$rc" ]] && grep -q 'OLLAMA_MODELS' "$rc" 2>/dev/null \
          || echo "export OLLAMA_MODELS=\"$OLLAMA_MODELS_DIR\"" >> "$rc"
      done
      export OLLAMA_MODELS="$OLLAMA_MODELS_DIR"
      ok "OLLAMA_MODELS → $OLLAMA_MODELS_DIR"
    else
      warn "OLLAMA_MODELS non défini (sera : $OLLAMA_MODELS_DIR)"
    fi
  fi

  # Démarrer le serveur si nécessaire
  REACHABLE=0
  curl -s http://127.0.0.1:11434/api/tags &>/dev/null && REACHABLE=1
  if [[ $REACHABLE -eq 0 && $CHECK_ONLY -eq 0 ]]; then
    echo -e "  ${GRAY}démarrage du serveur ollama...${RESET}"
    ollama serve &>/dev/null &
    for i in $(seq 1 20); do
      sleep 1
      curl -s http://127.0.0.1:11434/api/tags &>/dev/null && REACHABLE=1 && break
    done
  fi

  if [[ $REACHABLE -eq 0 ]]; then
    warn "serveur ollama injoignable sur 127.0.0.1:11434"
    hint "lancer : ollama serve"
  else
    ok "serveur ollama joignable"
    PRESENT=$(ollama list 2>/dev/null || true)
    for m in "${LLM_MODELS[@]}"; do
      if echo "$PRESENT" | grep -q "$m"; then
        ok "modèle $m"
      elif [[ $CHECK_ONLY -eq 1 ]]; then
        miss "modèle $m manquant"
      else
        echo -e "  ${GRAY}pull $m...${RESET}"
        ollama pull "$m" && ok "modèle $m" || miss "échec pull $m"
      fi
    done
  fi
fi

# =============================================================================
# Phase 4 — Blender (AppImage, cross-distro)
# =============================================================================
step "Blender (3D)"
BLENDER_EXE=""

if command -v blender &>/dev/null; then
  BLENDER_EXE=$(command -v blender)
  ok "Blender : $BLENDER_EXE"
else
  # Chercher une AppImage dans les emplacements courants
  for candidate in \
    "$HOME/Applications/blender"*".AppImage" \
    "$HOME/.local/bin/blender" \
    "/opt/blender/blender" \
    "/usr/local/bin/blender"; do
    # shellcheck disable=SC2086
    for f in $candidate; do
      [[ -x "$f" ]] && BLENDER_EXE="$f" && break 2
    done
  done

  if [[ -n "$BLENDER_EXE" ]]; then
    ok "Blender : $BLENDER_EXE"
  elif [[ $CHECK_ONLY -eq 1 ]]; then
    warn "Blender introuvable (pipeline 3D désactivé ; le cœur marche sans)"
    hint "télécharger l'AppImage sur https://www.blender.org/download/"
  else
    BLENDER_DIR="${DATA_ROOT:-$HOME/Applications}"
    mkdir -p "$BLENDER_DIR"
    echo -e "  ${GRAY}recherche de la dernière release Blender AppImage...${RESET}"
    BLENDER_URL=$(curl -s https://api.github.com/repos/blender/blender/releases/latest \
      | grep '"browser_download_url"' \
      | grep 'linux-x64.*\.tar\.xz"' \
      | head -1 | cut -d'"' -f4)
    if [[ -n "$BLENDER_URL" ]]; then
      ARCHIVE="$BLENDER_DIR/$(basename "$BLENDER_URL")"
      echo -e "  ${GRAY}téléchargement $(basename "$BLENDER_URL")...${RESET}"
      curl -L "$BLENDER_URL" -o "$ARCHIVE"
      tar -xf "$ARCHIVE" -C "$BLENDER_DIR"
      rm -f "$ARCHIVE"
      BLENDER_EXE=$(find "$BLENDER_DIR" -name 'blender' -type f | head -1)
      [[ -n "$BLENDER_EXE" ]] && ok "Blender : $BLENDER_EXE" || miss "extraction Blender : binaire introuvable"
    else
      warn "Blender : release GitHub introuvable — installer manuellement"
      hint "https://www.blender.org/download/"
    fi
  fi
fi

# =============================================================================
# Phase 5 — ComfyUI portable + modèles image
# =============================================================================
if [[ $SKIP_COMFYUI -eq 1 ]]; then
  step "ComfyUI (image) — SAUTÉ (--skip-comfyui)"
else
  step "ComfyUI portable + modèles image"
  COMFY_ROOT="$COMFYUI_DIR/ComfyUI"
  COMFY_MODELS_DIR=""

  if [[ -f "$COMFY_ROOT/main.py" ]]; then
    ok "ComfyUI déjà présent : $COMFY_ROOT"
    COMFY_MODELS_DIR="$COMFY_ROOT/models"
  elif [[ $CHECK_ONLY -eq 1 ]]; then
    miss "ComfyUI absent ($COMFY_ROOT)"
  else
    echo -e "  ${GRAY}clonage ComfyUI...${RESET}"
    mkdir -p "$COMFYUI_DIR"
    git clone --depth 1 https://github.com/comfyanonymous/ComfyUI.git "$COMFY_ROOT"
    if [[ -f "$COMFY_ROOT/main.py" ]]; then
      ok "ComfyUI cloné : $COMFY_ROOT"
      COMFY_MODELS_DIR="$COMFY_ROOT/models"
      # Venv ComfyUI
      python3 -m venv "$COMFY_ROOT/.venv"
      "$COMFY_ROOT/.venv/bin/pip" install -q torch torchvision --index-url https://download.pytorch.org/whl/cu121
      "$COMFY_ROOT/.venv/bin/pip" install -q -r "$COMFY_ROOT/requirements.txt"
      ok "dépendances ComfyUI installées"
    else
      miss "clone ComfyUI : structure inattendue"
    fi
  fi

  [[ -z "$COMFY_MODELS_DIR" ]] && COMFY_MODELS_DIR="${CORE_DIR}/models"

  # Modèles image
  IMAGE_MODELS=(
    "checkpoints|RealVisXL_V5.0_fp16.safetensors|https://huggingface.co/SG161222/RealVisXL_V5.0/resolve/main/RealVisXL_V5.0_fp16.safetensors|6938065488"
    "upscale_models|4x-UltraSharp.pth|https://huggingface.co/Kim2091/UltraSharp/resolve/main/4x-UltraSharp.pth|66961958"
  )
  for entry in "${IMAGE_MODELS[@]}"; do
    IFS='|' read -r sub name url expected <<< "$entry"
    dest="$COMFY_MODELS_DIR/$sub/$name"
    if [[ -f "$dest" ]]; then
      actual=$(stat -c%s "$dest" 2>/dev/null || stat -f%z "$dest")
      [[ "$actual" -eq "$expected" ]] && ok "déjà présent : $name" && continue
    fi
    if [[ $CHECK_ONLY -eq 1 ]]; then
      miss "$name absent"
    else
      mkdir -p "$(dirname "$dest")"
      echo -e "  ${GRAY}téléchargement $name (~$((expected/1024/1024)) Mo)...${RESET}"
      curl -L "$url" -o "$dest" && ok "$name" || miss "échec téléchargement $name"
    fi
  done
fi

# =============================================================================
# Phase 6 — backend Python (venv + requirements)
# =============================================================================
step "Backend (venv Python)"
VENV_DIR="$CORE_DIR/.venv"
VENV_PY="$VENV_DIR/bin/python"

if ! command -v python3 &>/dev/null; then
  miss "python3 introuvable"
elif [[ -x "$VENV_PY" ]]; then
  ok "venv déjà présent : $VENV_DIR"
elif [[ $CHECK_ONLY -eq 1 ]]; then
  miss "venv absent ($VENV_DIR)"
else
  echo -e "  ${GRAY}création venv + pip install...${RESET}"
  python3 -m venv "$VENV_DIR"
  "$VENV_PY" -m pip install --upgrade pip -q
  "$VENV_PY" -m pip install -r "$CORE_DIR/requirements.txt" -q
  [[ -x "$VENV_PY" ]] && ok "venv prêt : $VENV_DIR" || miss "échec création venv"
fi

# =============================================================================
# Phase 7 — core/.env Linux
# =============================================================================
step "Configuration (core/.env)"
ENV_PATH="$CORE_DIR/.env"

if [[ -f "$ENV_PATH" ]]; then
  ok ".env déjà présent (laissé tel quel)"
elif [[ $CHECK_ONLY -eq 1 ]]; then
  miss ".env absent"
else
  # Part du template canonique (secret-free), puis substitue les valeurs détectées
  # (sed sur place = pas de clé dupliquée). OLLAMA_MODELS n'est pas dans le template -> append.
  cp "$CORE_DIR/.env.example" "$ENV_PATH"
  [[ -n "${COMFY_MODELS_DIR:-}" ]] && sed -i "s#^COMFYUI_MODELS_DIR=.*#COMFYUI_MODELS_DIR=$COMFY_MODELS_DIR#" "$ENV_PATH"
  [[ -n "$BLENDER_EXE" ]]          && sed -i "s#^BLENDER_EXE=.*#BLENDER_EXE=$BLENDER_EXE#" "$ENV_PATH"
  [[ -n "${OLLAMA_MODELS:-}" ]]    && printf 'OLLAMA_MODELS=%s\n' "$OLLAMA_MODELS" >> "$ENV_PATH"
  ok ".env généré depuis .env.example : $ENV_PATH"
fi

# =============================================================================
# Bilan
# =============================================================================
echo ""
if [[ $FAIL -eq 1 ]]; then
  echo -e "${RED}== Bootstrap INCOMPLET : voir les [X] ci-dessus. ==${RESET}"
else
  echo -e "${GREEN}== Bootstrap OK. Démarrer le backend (depuis core/) :${RESET}"
  echo -e "${GRAY}   cd \"$CORE_DIR\" && .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000${RESET}"
fi
