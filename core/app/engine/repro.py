"""
repro.py — Utilitaires de reproductibilité des runs (chantier repro, phase capture).

Fournit les briques des blocs `repro` écrits dans les manifests Blender et
ComfyUI. La reproductibilité est définie en TROIS TIERS assumés — le
bit-exact GPU cross-machine n'existe pas (cuDNN non déterministe, versions
xformers, ordre des réductions flottantes) et prétendre l'inverse serait un
mensonge de manifest :

  Tier 1 — repro des PARAMÈTRES : tout ce qui entre est capturé (seed
           effectif, workflow résolu et son hash, noms/hash des modèles,
           versions ComfyUI/Blender, commit AAC). Atteignable à 100 %.
  Tier 2 — repro SÉMANTIQUE : hash canonique du scene_report (clés triées,
           floats arrondis, chemins exclus). Le `.blend` binaire n'est
           VOLONTAIREMENT PAS hashé : il embarque pointeurs internes et
           métadonnées de session — instable même à scène identique.
  Tier 3 — repro PERCEPTUELLE : sha256 (exact, même machine) + dHash
           (perceptuel, cross-machine) des images produites.

Garanties : chaque fonction est best-effort — jamais d'exception propagée
vers le chemin d'écriture des manifests (retour None en échec). Les sondes
coûteuses (version Blender, commit git) sont mémoïsées par process.

Config (env vars) :
- AAC_GIT_COMMIT        : commit stampé par le build (prioritaire sur git).
- COMFYUI_MODELS_DIR    : racine des modèles ComfyUI côté backend ; absente
                          (cas Docker : volume non monté) → sha des modèles
                          null, noms seuls.
- AAC_REPRO_HASH_MODELS : "0"/"false"/... pour ne pas CALCULER les hash de
                          modèles (fichiers de plusieurs Go — coût unique
                          par modèle, amorti par cache sidecar). Un cache
                          valide reste lu même désactivé.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional

AAC_GIT_COMMIT_ENV = "AAC_GIT_COMMIT"
COMFYUI_MODELS_DIR_ENV = "COMFYUI_MODELS_DIR"
REPRO_HASH_MODELS_ENV = "AAC_REPRO_HASH_MODELS"

_DISABLED_VALUES: frozenset[str] = frozenset({"0", "false", "no", "off"})

# Arrondi des floats dans la canonicalisation JSON : 6 décimales absorbent le
# bruit de sérialisation sans masquer un vrai changement de scène.
_FLOAT_PRECISION = 6

_SUBPROCESS_TIMEOUT = 10.0

# Mémos par process (sondes coûteuses, valeur stable sur la durée de vie du backend).
_UNSET = object()
_git_commit_memo: Any = _UNSET
_blender_version_memo: dict[str, Optional[str]] = {}
_ollama_version_memo: dict[str, Optional[str]] = {}


# ---------------------------------------------------------------------------
# Hachage — fichiers et JSON canonique
# ---------------------------------------------------------------------------

def sha256_file(path: str | Path | None) -> Optional[str]:
    """SHA256 hex d'un fichier, par blocs. None si absent/illisible."""
    if not path:
        return None
    try:
        digest = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def sha256_text(text: Optional[str]) -> Optional[str]:
    """SHA256 hex d'un contenu texte (UTF-8). Équivalent de sha256_file pour
    un contenu déjà en mémoire (ex. scene.py transmis à /reproduce)."""
    if text is None:
        return None
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _canonicalize(value: Any) -> Any:
    """Floats arrondis (et -0.0 normalisé), récursif. Les clés sont triées à la
    sérialisation (json sort_keys), pas ici."""
    if isinstance(value, float):
        rounded = round(value, _FLOAT_PRECISION)
        return 0.0 if rounded == 0.0 else rounded
    if isinstance(value, dict):
        return {k: _canonicalize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_canonicalize(item) for item in value]
    return value


def sha256_canonical_json(data: Any) -> Optional[str]:
    """
    SHA256 hex de la forme canonique d'une structure JSON : clés triées,
    séparateurs compacts, floats arrondis à 6 décimales. Deux structures
    égales au bruit flottant près partagent le même hash. None si data
    n'est pas sérialisable JSON.
    """
    try:
        canonical = json.dumps(
            _canonicalize(data),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# Version du bloc repro écrit dans les manifests. v2 : hash sémantique en
# WHITELIST (voir _semantic_scene_view) — v1 hashait tout le scene_report
# moins les chemins, ce qui embarquait des champs non sémantiques (mtime du
# preview, métriques pixel de la visual-QA, longueur du code dans l'ast_guard)
# et rendait le tier 2 non comparable entre deux exécutions identiques.
REPRO_VERSION = 2

_SEMANTIC_SCALAR_KEYS = (
    "template_name",
    "status",
    "object_count",
    "mesh_count",
    "camera_count",
    "light_count",
    "has_active_camera",
)
# framing_contract : la partie GÉOMÉTRIQUE (projection NDC déterministe de la
# scène). framing_divergence en est exclue : dérivée des PIXELS du preview
# (bbox perceptuelle) — c'est le territoire du tier 3.
_SEMANTIC_FRAMING_KEYS = (
    "status",
    "violations",
    "method",
    "screen_bbox",
    "occupancy",
    "center_u",
    "base_v",
    "in_frame",
    "depth_min",
    "thresholds",
)
# runtime_contract : le CHEMIN de correction emprunté (déterministe à entrées
# égales). before/after en sont exclus : mtime, tailles de fichiers, statuts
# de visual-QA pixel.
_SEMANTIC_RUNTIME_KEYS = (
    "status",
    "template_name",
    "corrections_applied",
    "correction_status",
    "initial_violations",
    "final_violations",
)


def _semantic_scene_view(report: dict[str, Any]) -> dict[str, Any]:
    """
    Projection WHITELIST du scene_report sur son contenu sémantique : ce que
    la scène EST (objets, comptes, template, cadrage géométrique, corrections
    appliquées), pas ce que son exécution a laissé comme traces (chemins,
    timestamps, métriques dérivées des pixels — mesuré live 2026-07-11 : deux
    exécutions du même scene.py donnent la même scène mais des mtimes et des
    floats de visual-QA différents).
    """
    view: dict[str, Any] = {key: report.get(key) for key in _SEMANTIC_SCALAR_KEYS}
    view["object_names"] = report.get("object_names")
    view["violations"] = report.get("violations")

    framing = report.get("framing_contract")
    if isinstance(framing, dict):
        view["framing_contract"] = {k: framing.get(k) for k in _SEMANTIC_FRAMING_KEYS}

    runtime = report.get("runtime_contract")
    if isinstance(runtime, dict):
        view["runtime_contract"] = {k: runtime.get(k) for k in _SEMANTIC_RUNTIME_KEYS}

    return view


def semantic_scene_report_hash(report: Any) -> Optional[str]:
    """
    Tier 2 — hash sémantique du scene_report : projection whitelist puis
    hash canonique. Deux runs produisant la même scène partagent ce hash,
    quels que soient request_id, machine, timestamps ou bruit pixel.
    None si le report n'est pas un dict non vide.
    """
    if not isinstance(report, dict) or not report:
        return None
    return sha256_canonical_json(_semantic_scene_view(report))


# ---------------------------------------------------------------------------
# Tier 3 — hash perceptuel d'image (dHash)
# ---------------------------------------------------------------------------

def dhash_image(path: str | Path | None, hash_size: int = 8) -> Optional[str]:
    """
    dHash (difference hash) : niveaux de gris, réduction (hash_size+1 × hash_size),
    bit = gradient horizontal. 64 bits → 16 hex. Implémenté avec Pillow seul
    (déjà en requirements) — pas de dépendance imagehash/numpy. Robuste au
    bruit GPU léger : deux rendus « même image » diffèrent de quelques bits
    là où le sha256 diverge totalement. None si le fichier n'est pas une
    image lisible.
    """
    if not path:
        return None
    try:
        from PIL import Image

        with Image.open(path) as img:
            gray = img.convert("L").resize(
                (hash_size + 1, hash_size), Image.Resampling.LANCZOS
            )
            pixels = list(gray.get_flattened_data())
    except Exception:  # noqa: BLE001 — fichier corrompu/format inconnu → None
        return None

    bits = 0
    width = hash_size + 1
    for row in range(hash_size):
        for col in range(hash_size):
            left = pixels[row * width + col]
            right = pixels[row * width + col + 1]
            bits = (bits << 1) | (1 if left < right else 0)
    return f"{bits:0{hash_size * hash_size // 4}x}"


def sha256_image_pixels(path: str | Path | None) -> Optional[str]:
    """
    SHA256 des PIXELS décodés (RGBA), pas du fichier : ComfyUI embarque le
    prompt dans les métadonnées PNG, donc deux images aux pixels identiques
    peuvent différer en octets. C'est CE hash qui prouve « même image » au
    replay ; le sha256 fichier ne prouve que « même octets ». None si le
    fichier n'est pas une image lisible.
    """
    if not path:
        return None
    try:
        from PIL import Image

        with Image.open(path) as img:
            data = img.convert("RGBA").tobytes()
    except Exception:  # noqa: BLE001 — fichier corrompu/format inconnu → None
        return None
    return hashlib.sha256(data).hexdigest()


def dhash_distance(a: Optional[str], b: Optional[str]) -> Optional[int]:
    """Distance de Hamming entre deux dHash hex. None si non comparables."""
    if not a or not b or len(a) != len(b):
        return None
    try:
        return bin(int(a, 16) ^ int(b, 16)).count("1")
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Tier 1 — sondes d'environnement (mémoïsées)
# ---------------------------------------------------------------------------

def aac_git_commit() -> Optional[str]:
    """
    Commit AAC : env AAC_GIT_COMMIT (stampé au build de l'image) prioritaire,
    sinon `git rev-parse HEAD` depuis la racine du repo (cas natif). None en
    conteneur sans stamp (pas de .git dans l'image). Mémoïsé.
    """
    global _git_commit_memo
    if _git_commit_memo is not _UNSET:
        return _git_commit_memo

    stamped = os.environ.get(AAC_GIT_COMMIT_ENV)
    if stamped and stamped.strip():
        _git_commit_memo = stamped.strip()
        return _git_commit_memo

    repo_root = Path(__file__).resolve().parents[3]
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT,
        )
        commit = proc.stdout.strip() if proc.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        commit = None

    _git_commit_memo = commit or None
    return _git_commit_memo


def blender_version() -> Optional[str]:
    """
    Première ligne de `blender --version` (ex. « Blender 4.1.1 »). Binaire
    résolu comme blender_client : env BLENDER_EXE, sinon `blender` du PATH.
    (Résolution dupliquée à dessein : blender_client importe artifact_manifest
    qui importe ce module — l'import inverse serait un cycle.) Mémoïsé par
    binaire. None si introuvable ou muet.
    """
    exe = (os.environ.get("BLENDER_EXE") or "").strip() or "blender"
    if exe in _blender_version_memo:
        return _blender_version_memo[exe]

    resolved = shutil.which(exe) or (exe if Path(exe).is_file() else None)
    version: Optional[str] = None
    if resolved:
        try:
            proc = subprocess.run(
                [resolved, "--version"],
                capture_output=True,
                text=True,
                timeout=_SUBPROCESS_TIMEOUT,
            )
            if proc.returncode == 0 and proc.stdout:
                first_line = proc.stdout.strip().splitlines()[0].strip()
                version = first_line or None
        except (OSError, subprocess.SubprocessError):
            version = None

    _blender_version_memo[exe] = version
    return version


def ollama_environment() -> dict[str, Any]:
    """Bloc provenance BYO Ollama : endpoint + version + modèles par rôle.

    Capture d'ENVIRONNEMENT best-effort : `version` est None si l'instance
    ne répond pas ; les modèles viennent de la config (aucun réseau). Ce
    bloc ne participe PAS aux environment_diffs du rejeu — ni le replay
    ComfyUI ni le replay Blender ne rappellent Ollama (workflow resoumis
    tel quel, scene.py rejoué tel quel), un écart Ollama n'explique donc
    jamais un verdict. Version mémoïsée par endpoint (une sonde réseau par
    process, même politique que blender_version).
    """
    from app.infra.ollama_runtime import get_ollama_base_url, resolved_model_summary

    base_url = get_ollama_base_url()
    if base_url not in _ollama_version_memo:
        import requests

        version: Optional[str] = None
        try:
            response = requests.get(f"{base_url}/api/version", timeout=3.0)
            if response.ok:
                raw = response.json().get("version")
                version = str(raw) if raw else None
        except (requests.RequestException, ValueError):
            version = None
        _ollama_version_memo[base_url] = version
    return {
        "base_url": base_url,
        "version": _ollama_version_memo[base_url],
        "models": resolved_model_summary(),
    }


def reset_probe_memos() -> None:
    """Réinitialise les mémos (tests uniquement)."""
    global _git_commit_memo
    _git_commit_memo = _UNSET
    _blender_version_memo.clear()
    _ollama_version_memo.clear()


# ---------------------------------------------------------------------------
# Tier 1 — hash des fichiers modèles (checkpoints, upscalers)
# ---------------------------------------------------------------------------

def _is_model_hashing_enabled() -> bool:
    raw = os.environ.get(REPRO_HASH_MODELS_ENV)
    if raw is None:
        return True
    return raw.strip().lower() not in _DISABLED_VALUES


def model_file_sha256(model_path: str | Path | None) -> Optional[str]:
    """
    SHA256 d'un fichier modèle (plusieurs Go) avec cache sidecar
    `<fichier>.sha256.json` validé par (taille, mtime) — le calcul ne paie
    qu'une fois par fichier. AAC_REPRO_HASH_MODELS=0 désactive le CALCUL
    (un cache valide reste lu : gratuit). None si absent/illisible.
    """
    if not model_path:
        return None
    path = Path(model_path)
    try:
        stat = path.stat()
    except OSError:
        return None

    sidecar = path.with_name(path.name + ".sha256.json")
    try:
        cached = json.loads(sidecar.read_text(encoding="utf-8"))
        if (
            isinstance(cached, dict)
            and cached.get("size") == stat.st_size
            and cached.get("mtime") == stat.st_mtime
            and isinstance(cached.get("sha256"), str)
        ):
            return cached["sha256"]
    except (OSError, ValueError):
        pass

    if not _is_model_hashing_enabled():
        return None

    digest = sha256_file(path)
    if digest is None:
        return None

    try:
        sidecar.write_text(
            json.dumps({"sha256": digest, "size": stat.st_size, "mtime": stat.st_mtime}),
            encoding="utf-8",
        )
    except OSError:
        pass  # cache best-effort : répertoire modèles possiblement read-only

    return digest


def comfyui_models_dir() -> Optional[Path]:
    """Racine des modèles ComfyUI vue du backend, si configurée ET lisible.
    En Docker le volume n'est pas monté côté backend → None (noms sans hash)."""
    raw = os.environ.get(COMFYUI_MODELS_DIR_ENV)
    if not raw or not raw.strip():
        return None
    path = Path(raw.strip())
    return path if path.is_dir() else None
