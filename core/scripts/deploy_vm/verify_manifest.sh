#!/usr/bin/env bash
# AI_ASSISTANT_CORE — VM-side manifest verifier
# ----------------------------------------------------------
# Lit un manifest "sha256  chemin_relatif" et recalcule chaque hash
# par rapport à un répertoire racine. Imprime un verdict lisible.
#
# Usage :
#   verify_manifest.sh <manifest_path> <root_dir>
#
# Sortie :
#   - imprime "MATCH" si tous les fichiers correspondent
#   - imprime "MISMATCH" suivi de la liste des écarts sinon
#
# Codes de retour :
#   0 = MATCH
#   1 = MISMATCH (au moins un fichier diverge ou est absent)
#   2 = erreur d'invocation, environnement invalide,
#       ou chemin manifest jugé non sûr (absolu, ou contenant "..")
#
# Ce script ne modifie rien. Lecture seule.
# ----------------------------------------------------------

set -u

if [ "$#" -ne 2 ]; then
  echo "ERROR: usage: $0 <manifest_path> <root_dir>" >&2
  exit 2
fi

MANIFEST="$1"
ROOT="$2"

if [ ! -f "$MANIFEST" ]; then
  echo "ERROR: manifest not found: $MANIFEST" >&2
  exit 2
fi

if [ ! -d "$ROOT" ]; then
  echo "ERROR: root dir not found: $ROOT" >&2
  exit 2
fi

if ! command -v sha256sum >/dev/null 2>&1; then
  echo "ERROR: sha256sum not available on this VM" >&2
  exit 2
fi

mismatches=()
missing=()
checked=0

# Lecture ligne par ligne. Format attendu :
#   <sha256_hex>  <chemin_relatif>
# Lignes vides et commentaires (#) ignorés.
while IFS= read -r line || [ -n "$line" ]; do
  case "$line" in
    ''|\#*) continue ;;
  esac

  expected_hash="$(printf '%s' "$line" | awk '{print $1}')"
  rel_path="$(printf '%s' "$line" | awk '{ $1=""; sub(/^[[:space:]]+/, ""); print }')"

  if [ -z "$expected_hash" ] || [ -z "$rel_path" ]; then
    echo "WARN: skipping malformed line: $line" >&2
    continue
  fi

  # Garde de sécurité : refus dur des chemins manifest suspects.
  # On refuse :
  #   - tout chemin absolu (commence par "/")
  #   - tout chemin contenant ".." (segment de remontée)
  # Volontairement strict : aucun cas légitime de la whitelist
  # ne produit ce genre de chemin.
  case "$rel_path" in
    /*|*..*)
      echo "ERROR: unsafe path in manifest: $rel_path" >&2
      exit 2
      ;;
  esac

  abs_path="$ROOT/$rel_path"

  if [ ! -f "$abs_path" ]; then
    missing+=("$rel_path")
    continue
  fi

  actual_hash="$(sha256sum "$abs_path" | awk '{print $1}')"
  checked=$((checked + 1))

  if [ "$actual_hash" != "$expected_hash" ]; then
    mismatches+=("$rel_path")
  fi
done < "$MANIFEST"

echo "checked: $checked"

if [ "${#missing[@]}" -eq 0 ] && [ "${#mismatches[@]}" -eq 0 ]; then
  echo "MATCH"
  exit 0
fi

echo "MISMATCH"

if [ "${#missing[@]}" -gt 0 ]; then
  echo "missing:"
  for f in "${missing[@]}"; do
    echo "  - $f"
  done
fi

if [ "${#mismatches[@]}" -gt 0 ]; then
  echo "differs:"
  for f in "${mismatches[@]}"; do
    echo "  - $f"
  done
fi

exit 1