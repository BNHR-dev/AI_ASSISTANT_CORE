"""C2 — Authentification de l'API (bearer token statique).

Single-user local : un token partagé suffit. Le mécanisme s'aligne sur les
clients OpenAI-compatibles (Open-WebUI) qui envoient déjà
`Authorization: Bearer <token>`.

Trois postures via `AAC_API_AUTH_MODE` :
- (non posé) → `presence` : auth appliquée SI `AAC_API_TOKEN` est posé, sinon
  API ouverte + avertissement au démarrage (confort loopback dev).
- `off`      → auth jamais appliquée (même si un token est posé).
- `required` → auth appliquée ET **refus de démarrer** si `AAC_API_TOKEN` est
  absent ou invalide (fail-closed). Mode du profil exposé/public.

Le token n'est JAMAIS loggé. Comparaison en temps constant
(`secrets.compare_digest`). 401 + `WWW-Authenticate: Bearer` sur refus.

Variables d'environnement associées (lues au démarrage par main.create_app) :
- `AAC_CONSOLE_ENABLED` : monte la console (/console/*) — défaut activé en
  local, à poser à 0 dans le profil exposé (la console n'est pas authentifiée).
- profil exposé (`required`) : /docs, /redoc, /openapi.json désactivés.
"""

from __future__ import annotations

import os
import secrets
import sys

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

AUTH_MODE_ENV = "AAC_API_AUTH_MODE"
AUTH_TOKEN_ENV = "AAC_API_TOKEN"
CONSOLE_ENABLED_ENV = "AAC_CONSOLE_ENABLED"

MODE_OFF = "off"
MODE_PRESENCE = "presence"      # défaut quand AAC_API_AUTH_MODE non posé
MODE_REQUIRED = "required"
_EXPLICIT_MODES = (MODE_OFF, MODE_REQUIRED)

# Longueur minimale exigée pour considérer un token comme valide en mode
# `required` (refus de démarrage sinon). Garde-fou contre un token trivial.
MIN_TOKEN_LEN = 16

_FALSEY = ("0", "false", "no", "off", "")


class AuthConfigError(RuntimeError):
    """Configuration d'auth incohérente → refus de démarrage (mode required)."""


def current_auth_mode() -> str:
    """Mode courant. Valeur explicite `off`/`required` respectée ; tout le
    reste (non posé, valeur inconnue) → `presence`."""
    raw = os.getenv(AUTH_MODE_ENV, "").strip().lower()
    return raw if raw in _EXPLICIT_MODES else MODE_PRESENCE


def _configured_token() -> str:
    return (os.getenv(AUTH_TOKEN_ENV) or "").strip()


def _token_is_valid(token: str) -> bool:
    return len(token) >= MIN_TOKEN_LEN


def auth_is_enforced() -> bool:
    """L'auth est-elle réellement appliquée sur les routes protégées ?"""
    mode = current_auth_mode()
    if mode == MODE_OFF:
        return False
    if mode == MODE_REQUIRED:
        return True
    # presence : appliquée seulement si un token est posé.
    return bool(_configured_token())


def console_enabled() -> bool:
    """La console (/console/*) doit-elle être montée ? Défaut: oui (local).
    Profil exposé : poser AAC_CONSOLE_ENABLED=0 (console non authentifiée)."""
    raw = os.getenv(CONSOLE_ENABLED_ENV)
    if raw is None:
        return True
    return raw.strip().lower() not in _FALSEY


def docs_enabled() -> bool:
    """/docs, /redoc, /openapi.json : désactivés dans le profil exposé
    (`required`), actifs en local (presence/off)."""
    return current_auth_mode() != MODE_REQUIRED


def validate_startup_auth() -> None:
    """Cohérence au démarrage. En mode `required`, lève `AuthConfigError`
    (→ refus de démarrer) si le token est absent ou invalide. Ne logge
    jamais le token."""
    if current_auth_mode() != MODE_REQUIRED:
        return
    token = _configured_token()
    if not token:
        raise AuthConfigError(
            f"{AUTH_MODE_ENV}=required mais {AUTH_TOKEN_ENV} absent : "
            "refus de démarrage (fail-closed)."
        )
    if not _token_is_valid(token):
        raise AuthConfigError(
            f"{AUTH_MODE_ENV}=required mais {AUTH_TOKEN_ENV} invalide "
            f"(longueur < {MIN_TOKEN_LEN}) : refus de démarrage (fail-closed)."
        )


def log_auth_posture() -> None:
    """Trace la posture au démarrage (jamais le token lui-même)."""
    mode = current_auth_mode()
    enforced = auth_is_enforced()
    print(
        f"[auth] mode={mode} enforced={str(enforced).lower()} "
        f"console_enabled={str(console_enabled()).lower()} "
        f"docs_enabled={str(docs_enabled()).lower()}",
        file=sys.stderr,
    )
    if not enforced:
        print(
            "[auth] WARNING API NON authentifiée. Ne pas exposer hors loopback. "
            f"Poser {AUTH_TOKEN_ENV} (+ {AUTH_MODE_ENV}=required avant exposition).",
            file=sys.stderr,
        )
    if enforced and console_enabled():
        print(
            "[auth] WARNING console montée et NON authentifiée alors que l'auth "
            f"API est active : sur un serveur exposé, poser {CONSOLE_ENABLED_ENV}=0.",
            file=sys.stderr,
        )


_bearer_scheme = HTTPBearer(auto_error=False)


def require_api_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> None:
    """Dépendance FastAPI appliquée aux routes protégées.

    Laisse passer si l'auth n'est pas appliquée (mode off, ou presence sans
    token). Sinon exige un `Authorization: Bearer <token>` exact (comparaison
    constant-time) ; 401 + `WWW-Authenticate: Bearer` à défaut. Ne compare
    jamais contre un token vide (défense en profondeur si le garde-fou de
    démarrage a été contourné)."""
    if not auth_is_enforced():
        return

    expected = _configured_token()
    provided = (
        credentials.credentials
        if credentials is not None and credentials.scheme.lower() == "bearer"
        else ""
    )
    if not (expected and secrets.compare_digest(provided, expected)):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
