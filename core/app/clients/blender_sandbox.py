"""C1c — Sandbox système des subprocess Blender (bubblewrap).

Contexte sécurité (voir 05_AUDITS/.../05_NOTES_CYBERSECURITE.md) :
C1a/C1b couvrent le gate AST bloquant + `--factory-startup`/`--disable-autoexec`.
La faille résiduelle = un `scene.py` généré **obfusqué** qui passe le gate AST.
C1c confine l'exécution OS-level via `bwrap` : pas de réseau, pas de home,
système en lecture seule, écriture limitée au dossier output canonique.

Design **asymétrique** (deux profils) :
- `strict`  : build de scène (exécute le code LLM) + inspection. Aucun GPU.
- `render`  : rendu EEVEE. Ajoute `/sys` (ro) + les devices GPU précis présents
              (`/dev/dri/renderD*`, `/dev/nvidia*`). Reste sans réseau ni home.

Le module est **pur** : `build_sandbox_plan` compose seulement l'argv `bwrap`
et se laisse tester sans exécuter bwrap. La découverte des devices GPU et la
résolution du backend sont isolées dans des fonctions monkeypatchables.

Modes (`AAC_BLENDER_SANDBOX`) :
- `auto`    (défaut dev) : bwrap si disponible/utilisable, sinon exec direct + warning.
- `require` : échec fermé (`SandboxError`) si bwrap absent/inutilisable.
              **Mode obligatoire avant toute exposition réseau.**
- `off`     : passthrough (debug/CI), aucun sandbox.

Risque résiduel hors C1c (documenté) : C1c ne pose **pas** de limite CPU/RAM.
Un script qui passe le gate peut encore consommer du CPU/mémoire jusqu'au
timeout. Les quotas de ressources (cgroups / `systemd-run` `MemoryMax`...)
sont un durcissement séparé, hors périmètre C1c.
"""

from __future__ import annotations

import functools
import glob
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

SANDBOX_ENV_VAR = "AAC_BLENDER_SANDBOX"
_DEFAULT_MODE = "auto"
_VALID_MODES = ("auto", "require", "off")

BACKEND_BWRAP = "bwrap"
BACKEND_NONE = "none"

PROFILE_STRICT = "strict"
PROFILE_RENDER = "render"

# Variables d'environnement explicitement laissées passer DANS le sandbox.
# Tout le reste est effacé (`--clearenv`) : aucun token, secret, SSH_AUTH_SOCK,
# variable Docker/DBus n'atteint le code généré. HOME/PATH/TMPDIR sont réinjectés
# en valeurs neutres ; seules quelques variables de locale sont reprises de l'hôte.
_ENV_LOCALE_ALLOWLIST = ("LANG", "LC_ALL", "LC_CTYPE", "LC_NUMERIC", "LC_MESSAGES")

# Globs des devices GPU à exposer dans le profil `render` (et uniquement lui).
# Pas de bind global de `/dev` : seuls ces nodes précis sont montés.
_GPU_DEVICE_GLOBS = ("/dev/dri/renderD*", "/dev/nvidia*")


class SandboxError(RuntimeError):
    """Le sandbox est requis (mode=require) mais indisponible/inutilisable,
    ou le chemin output sort de la racine autorisée (échappement)."""


@dataclass(frozen=True)
class SandboxPlan:
    """Résultat de `build_sandbox_plan` : l'argv final + métadonnées de traçabilité."""

    argv: list[str]
    backend: str          # BACKEND_BWRAP | BACKEND_NONE
    profile: str          # PROFILE_STRICT | PROFILE_RENDER
    requested_mode: str    # auto | require | off
    active: bool           # True si le sandbox enveloppe réellement la commande

    def log_line(self) -> str:
        """Trace structurée minimale (C1c #7) : backend, profil, mode, activation."""
        return (
            f"[blender_sandbox] backend={self.backend} profile={self.profile} "
            f"requested_mode={self.requested_mode} active={str(self.active).lower()}"
        )


def current_mode() -> str:
    """Mode courant lu depuis l'env, normalisé. Valeur inconnue → défaut `auto`."""
    raw = os.getenv(SANDBOX_ENV_VAR, _DEFAULT_MODE).strip().lower()
    return raw if raw in _VALID_MODES else _DEFAULT_MODE


@functools.lru_cache(maxsize=1)
def _resolve_bwrap_exe() -> str | None:
    """Chemin de `bwrap`, ou None. Caché (le PATH ne change pas en cours de run)."""
    return shutil.which("bwrap")


def _base_ro_args() -> list[str]:
    """Binds read-only communs aux deux profils : système en lecture seule.

    `/usr` + `/etc` (ld.so.cache, fonts, glvnd) en ro, et les symlinks
    `/lib`,`/lib64`,`/bin` → `usr/*` attendus par l'éditeur de liens.
    """
    return [
        "--ro-bind", "/usr", "/usr",
        "--ro-bind", "/etc", "/etc",
        "--symlink", "usr/lib", "/lib",
        "--symlink", "usr/lib64", "/lib64",
        "--symlink", "usr/bin", "/bin",
    ]


def _isolation_args() -> list[str]:
    """Namespaces + env neutre, communs aux deux profils.

    `--unshare-net` est **explicite** dans les deux profils (C1c #2) : aucun
    accès réseau, jamais. `--clearenv` efface l'env hôte ; on réinjecte HOME/
    PATH/TMPDIR neutres + locale allowlistée (C1c #1).
    """
    args = [
        "--proc", "/proc",
        "--dev", "/dev",            # devtmpfs minimal NEUF (pas un bind de l'hôte)
        "--tmpfs", "/tmp",
        "--unshare-net",            # explicite — pas de réseau
        "--unshare-pid",            # PID ns : tuer bwrap détruit tout le groupe (C1c #6)
        "--unshare-ipc",
        "--unshare-uts",
        "--die-with-parent",
        "--new-session",
        "--clearenv",
        "--setenv", "HOME", "/tmp",
        "--setenv", "PATH", "/usr/bin:/bin",
        "--setenv", "TMPDIR", "/tmp",
    ]
    for var in _ENV_LOCALE_ALLOWLIST:
        val = os.environ.get(var)
        if val is not None:
            args += ["--setenv", var, val]
    return args


def gpu_device_paths() -> list[str]:
    """Devices GPU présents à exposer dans le profil `render`.

    Isolé pour être monkeypatchable dans les tests hermétiques (une machine CI
    sans GPU renvoie une liste vide). Aucun bind global de `/dev`.
    """
    paths: list[str] = []
    for pattern in _GPU_DEVICE_GLOBS:
        paths.extend(sorted(glob.glob(pattern)))
    # Dédup en gardant l'ordre, ne garder que ce qui existe réellement.
    seen: set[str] = set()
    unique = []
    for p in paths:
        if p not in seen and os.path.exists(p):
            seen.add(p)
            unique.append(p)
    return unique


def _gpu_args() -> list[str]:
    """`/sys` (ro) + dev-bind des devices GPU précis. Profil `render` uniquement."""
    args = ["--ro-bind", "/sys", "/sys"]
    for dev in gpu_device_paths():
        args += ["--dev-bind", dev, dev]
    return args


def _default_output_root() -> str:
    """Racine autorisée pour les outputs (alignée sur `BLENDER_OUTPUT_DIR`)."""
    return os.getenv("BLENDER_OUTPUT_DIR", "outputs/blender").strip() or "outputs/blender"


def _validate_output_dir(output_dir: str, output_root: str) -> Path:
    """Canonise `output_dir` (résout les symlinks) et exige qu'il soit sous la
    racine autorisée. Lève `SandboxError` sinon — y compris sur échappement par
    symlink, puisque `resolve()` suit les liens avant la comparaison (C1c #4)."""
    root = Path(output_root).resolve()
    target = Path(output_dir).resolve()
    if target != root and not target.is_relative_to(root):
        raise SandboxError(
            f"output_dir hors racine sandbox autorisée : {target} pas sous {root}"
        )
    return target


def _passthrough(argv, profile, mode) -> SandboxPlan:
    return SandboxPlan(
        argv=list(argv),
        backend=BACKEND_NONE,
        profile=profile,
        requested_mode=mode,
        active=False,
    )


def build_sandbox_plan(
    blender_argv,
    *,
    output_dir: str,
    profile: str,
    output_root: str | None = None,
    extra_rw_paths=(),
    extra_ro_paths=(),
) -> SandboxPlan:
    """Compose l'argv `bwrap` enveloppant `blender_argv` selon le profil.

    Args:
        blender_argv : la commande Blender complète, `argv[0]` = exécutable.
        output_dir   : dossier d'écriture autorisé (monté rw), validé sous racine.
        profile      : PROFILE_STRICT (build/inspect) | PROFILE_RENDER (rendu GPU).
        output_root  : racine autorisée (défaut = `BLENDER_OUTPUT_DIR`).
        extra_rw_paths : chemins rw supplémentaires **fournis par le framework**
                         (ex. fichier rapport temporaire de l'inspection), montés
                         APRÈS le tmpfs pour primer dessus. Jamais des chemins
                         contrôlés par le code généré.
        extra_ro_paths : chemins ro supplémentaires fournis par le framework.

    Returns:
        SandboxPlan — `argv` à passer tel quel à `subprocess.run`.

    Raises:
        SandboxError : mode=require et bwrap indisponible/inutilisable, ou
                       `output_dir` échappe la racine autorisée.
    """
    if profile not in (PROFILE_STRICT, PROFILE_RENDER):
        raise ValueError(f"profil sandbox inconnu : {profile!r}")

    blender_argv = list(blender_argv)
    mode = current_mode()

    # `off` : opt-out explicite (debug/CI), aucune validation ni sandbox.
    if mode == "off":
        return _passthrough(blender_argv, profile, mode)

    exe = _resolve_bwrap_exe()
    usable = bool(exe) and _bwrap_usable()
    if not usable:
        if mode == "require":
            raise SandboxError(
                "bwrap indisponible ou inutilisable et AAC_BLENDER_SANDBOX=require "
                "(exposition réseau interdite sans sandbox effectif)."
            )
        # auto : dégradé toléré en dev local — exec direct, mais on le signale fort.
        print(
            "[blender_sandbox] WARNING bwrap indisponible — exécution NON "
            "sandboxée (AAC_BLENDER_SANDBOX=auto). Passez en `require` avant "
            "toute exposition réseau.",
            file=sys.stderr,
        )
        return _passthrough(blender_argv, profile, mode)

    # Validation du chemin output (fail-closed sur échappement, y compris symlink).
    root = output_root if output_root is not None else _default_output_root()
    canon_output = _validate_output_dir(output_dir, root)

    args: list[str] = [exe]
    args += _base_ro_args()

    # L'exécutable Blender hors /usr → bind ro de son dossier (install canonique
    # = /usr/bin/blender, déjà couvert). Les installs custom hors /usr peuvent
    # nécessiter des binds additionnels de leurs libs (documenté).
    exe_path = Path(blender_argv[0])
    try:
        exe_real = exe_path.resolve()
    except OSError:
        exe_real = exe_path
    if not str(exe_real).startswith("/usr/"):
        exe_dir = str(exe_real.parent)
        args += ["--ro-bind", exe_dir, exe_dir]

    args += _isolation_args()

    # Dossier output en rw (couvre scene.py / scene.blend / preview.png /
    # scripts temporaires écrits par le pipeline).
    args += ["--bind", str(canon_output), str(canon_output)]

    # Binds framework additionnels — APRÈS le tmpfs pour primer dessus.
    for p in extra_ro_paths:
        ap = str(Path(p).resolve())
        args += ["--ro-bind", ap, ap]
    for p in extra_rw_paths:
        ap = str(Path(p).resolve())
        args += ["--bind", ap, ap]

    if profile == PROFILE_RENDER:
        args += _gpu_args()

    args += ["--", *blender_argv]

    return SandboxPlan(
        argv=args,
        backend=BACKEND_BWRAP,
        profile=profile,
        requested_mode=mode,
        active=True,
    )


@functools.lru_cache(maxsize=1)
def _bwrap_usable() -> bool:
    """Probe caché : bwrap peut-il réellement créer nos namespaces ?

    `which bwrap` ne suffit pas : les user namespaces peuvent être désactivés
    (sysctl, conteneur restreint). On lance une fois la commande minimale avec
    les unshare réellement utilisés ; si elle échoue, bwrap est `inutilisable`.
    """
    exe = _resolve_bwrap_exe()
    if not exe:
        return False
    try:
        proc = subprocess.run(
            [exe, *_base_ro_args(), "--proc", "/proc", "--dev", "/dev",
             "--unshare-net", "--unshare-pid", "--die-with-parent",
             "--", "/usr/bin/true"],
            capture_output=True,
            timeout=10,
        )
        return proc.returncode == 0
    except Exception:
        return False
