#!/usr/bin/env bash
# install-aac.sh — lanceur AAC Linux. Equivalent de Install-AAC.bat sur Windows.
# Passe tous les arguments à bootstrap.sh dans le même dossier.
#
# Usage :
#   ./install-aac.sh                        installation complète
#   ./install-aac.sh --data-root /mnt/ai    installation dans /mnt/ai/
#   ./install-aac.sh --check-only           mode doctor, rien n'est installé
#   ./install-aac.sh --skip-comfyui         saute ComfyUI

set -euo pipefail
exec "$(dirname "${BASH_SOURCE[0]}")/bootstrap.sh" "$@"
