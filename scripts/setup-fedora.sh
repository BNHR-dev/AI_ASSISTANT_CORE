#!/usr/bin/env bash
# setup-fedora.sh — prépare un poste Fedora pour le dev AI_ASSISTANT_CORE.
#
# IDEMPOTENT & PRUDENT : ré-exécutable sans danger ; n'effectue AUCUNE action
# destructive (pas de partition, pas de boot/Secure Boot, pas de .env, pas de
# pull de modèles, pas de `docker compose up`). À exécuter SUR Fedora, après l'install.
#
# Usage :
#   ./setup-fedora.sh [--yes] [--with-nvidia-driver] [--dry-run]
#
#   --yes                 ne pas demander de confirmation interactive
#   --with-nvidia-driver  installe AUSSI le driver NVIDIA (akmod-nvidia).
#                         ⚠️ touche au noyau + Secure Boot/MOK -> à valider à part.
#   --dry-run             affiche les actions sans les exécuter.

set -euo pipefail

ASSUME_YES=0
WITH_NVIDIA_DRIVER=0
DRY_RUN=0

for arg in "$@"; do
  case "$arg" in
    --yes|-y)             ASSUME_YES=1 ;;
    --with-nvidia-driver) WITH_NVIDIA_DRIVER=1 ;;
    --dry-run)            DRY_RUN=1 ;;
    *) echo "Option inconnue: $arg" >&2; exit 2 ;;
  esac
done

log()  { printf '\033[1;34m[setup]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[warn ]\033[0m %s\n' "$*"; }

run() {
  if [ "$DRY_RUN" -eq 1 ]; then
    printf '\033[2m[dry ] %s\033[0m\n' "$*"
  else
    bash -c "$*"
  fi
}

confirm() {
  [ "$ASSUME_YES" -eq 1 ] && return 0
  read -r -p "$1 [y/N] " ans
  [ "$ans" = "y" ] || [ "$ans" = "Y" ]
}

require_fedora() {
  if [ ! -f /etc/fedora-release ]; then
    echo "Ce script cible Fedora (/etc/fedora-release introuvable)." >&2
    exit 1
  fi
  log "Fedora détecté : $(cat /etc/fedora-release)"
}

pkg_installed() { rpm -q "$1" >/dev/null 2>&1; }

dnf_install() {
  # installe uniquement les paquets absents (idempotent)
  local to_install=()
  local p
  for p in "$@"; do
    if pkg_installed "$p"; then
      log "déjà présent : $p"
    else
      to_install+=("$p")
    fi
  done
  if [ "${#to_install[@]}" -gt 0 ]; then
    log "installation : ${to_install[*]}"
    run "sudo dnf install -y ${to_install[*]}"
  fi
}

setup_rpmfusion() {
  if pkg_installed rpmfusion-free-release && pkg_installed rpmfusion-nonfree-release; then
    log "RPM Fusion déjà configuré"
    return 0
  fi
  confirm "Configurer les dépôts RPM Fusion (free + nonfree) ?" || { warn "RPM Fusion ignoré"; return 0; }
  local ver
  ver="$(rpm -E %fedora)"
  run "sudo dnf install -y https://mirrors.rpmfusion.org/free/fedora/rpmfusion-free-release-${ver}.noarch.rpm https://mirrors.rpmfusion.org/nonfree/fedora/rpmfusion-nonfree-release-${ver}.noarch.rpm"
}

setup_dev_tools() {
  confirm "Installer les outils de dev (git, zsh, CLI modernes, python) ?" || { warn "Outils dev ignorés"; return 0; }
  dnf_install git git-lfs zsh util-linux-user \
    ripgrep fd-find bat fzf zoxide btop jq \
    python3 python3-pip python3-virtualenv \
    gcc make
  if command -v starship >/dev/null 2>&1; then
    log "starship déjà présent"
  elif confirm "Installer Starship (prompt) via l'installeur officiel ?"; then
    run "curl -fsSL https://starship.rs/install.sh | sh -s -- -y -b \"$HOME/.local/bin\""
  fi
}

setup_docker() {
  if command -v docker >/dev/null 2>&1; then
    log "docker déjà présent"
  elif confirm "Installer Docker CE (dépôt officiel) ?"; then
    run "sudo dnf -y install dnf-plugins-core"
    run "sudo dnf config-manager --add-repo https://download.docker.com/linux/fedora/docker-ce.repo"
    dnf_install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    run "sudo systemctl enable --now docker"
  else
    warn "Docker ignoré"
    return 0
  fi
  if id -nG "$USER" | tr ' ' '\n' | grep -qx docker; then
    log "utilisateur déjà dans le groupe docker"
  elif confirm "Ajouter $USER au groupe docker (nécessite un re-login) ?"; then
    run "sudo usermod -aG docker \"$USER\""
  fi
}

setup_nvidia_container_toolkit() {
  if pkg_installed nvidia-container-toolkit; then
    log "nvidia-container-toolkit déjà présent"
    return 0
  fi
  confirm "Installer nvidia-container-toolkit (GPU dans les conteneurs) ?" || { warn "container-toolkit ignoré"; return 0; }
  run "curl -fsSL https://nvidia.github.io/libnvidia-container/stable/rpm/nvidia-container-toolkit.repo | sudo tee /etc/yum.repos.d/nvidia-container-toolkit.repo >/dev/null"
  dnf_install nvidia-container-toolkit
  warn "Étape manuelle (non auto) : sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker"
}

setup_nvidia_driver() {
  if [ "$WITH_NVIDIA_DRIVER" -ne 1 ]; then
    warn "Driver NVIDIA NON installé (touche au noyau + Secure Boot/MOK)."
    warn "Quand validé : relancer avec --with-nvidia-driver, puis voir core/docs/SETUP_LINUX.md (section MOK)."
    return 0
  fi
  warn "⚠️ Si Secure Boot est actif, un enrôlement MOK sera demandé au prochain reboot."
  confirm "Confirmer l'installation de akmod-nvidia ?" || { warn "Driver NVIDIA ignoré"; return 0; }
  dnf_install akmod-nvidia xorg-x11-drv-nvidia-cuda
  warn "Reboot nécessaire. Vérifs ensuite : mokutil --sb-state ; nvidia-smi"
}

setup_python_venv() {
  local core_dir venv
  core_dir="$(cd "$(dirname "$0")/../core" && pwd)"
  venv="$core_dir/.venv"
  if [ -d "$venv" ]; then
    log "venv déjà présent : $venv"
  elif confirm "Créer le venv Python dans $venv ?"; then
    run "python3 -m venv \"$venv\""
  else
    warn "venv ignoré"
    return 0
  fi
  if [ -f "$core_dir/requirements.txt" ] && confirm "Installer les dépendances (requirements.txt) ?"; then
    run "\"$venv/bin/pip\" install --upgrade pip"
    run "\"$venv/bin/pip\" install -r \"$core_dir/requirements.txt\""
  fi
}

main() {
  require_fedora
  log "Périmètre : dev tooling + Docker + container-toolkit + venv. AUCUNE action disque/boot/.env."
  setup_rpmfusion
  setup_dev_tools
  setup_docker
  setup_nvidia_container_toolkit
  setup_nvidia_driver
  setup_python_venv
  log "Terminé."
  log "Étapes manuelles restantes :"
  log "  1) copier core/env.linux.example -> core/.env (et ajuster)"
  log "  2) configurer le runtime GPU docker (nvidia-ctk, cf. ci-dessus)"
  log "  3) docker compose -f core/docker-compose.linux.yml up"
}

main "$@"
