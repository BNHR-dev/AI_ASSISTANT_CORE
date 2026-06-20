"""C2 — Tests de l'authentification API (bearer token + postures + gating).

Couvre : routes protégées (401 sans/with mauvais token, 200 avec bon token),
/health ouvert, console non gardée par le token, modes off/presence/required,
refus de démarrage en `required` sans token valide, désactivation docs et
console dans le profil exposé.

Par défaut (aucun AAC_API_*), l'auth n'est pas appliquée → les autres tests de
la suite ne sont pas impactés.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.auth import AuthConfigError, validate_startup_auth
from app.main import app, create_app

VALID = "k" * 32  # >= MIN_TOKEN_LEN
client = TestClient(app)

# (méthode, chemin, corps valide) pour chaque famille de routes protégées.
PROTECTED = [
    ("get", "/debug/canonical", None),
    ("get", "/health/runtime", None),
    ("get", "/v1/models", None),
    ("post", "/route", {"message": "x"}),
    ("post", "/execute", {"message": "x"}),
    ("post", "/v1/chat/completions", {"messages": [{"role": "user", "content": "hi"}]}),
]


def _call(c, method, path, body, headers=None):
    if method == "get":
        return c.get(path, headers=headers)
    return c.post(path, json=(body or {}), headers=headers)


@pytest.fixture
def presence_with_token(monkeypatch):
    """Mode presence (défaut) + token posé → auth appliquée."""
    monkeypatch.delenv("AAC_API_AUTH_MODE", raising=False)
    monkeypatch.setenv("AAC_API_TOKEN", VALID)


# --------------------------------------------------------------------------- #
# Négatif : routes protégées refusées sans / avec mauvais token
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("method,path,body", PROTECTED)
def test_protected_route_401_without_token(presence_with_token, method, path, body):
    r = _call(client, method, path, body)
    assert r.status_code == 401, path
    assert r.headers.get("www-authenticate") == "Bearer"


@pytest.mark.parametrize("method,path,body", PROTECTED)
def test_protected_route_401_with_wrong_token(presence_with_token, method, path, body):
    r = _call(client, method, path, body, headers={"Authorization": "Bearer wrong-token-xxxxxxxx"})
    assert r.status_code == 401, path


# --------------------------------------------------------------------------- #
# Positif : 200 avec le bon token (routes pures, sans pipeline lourd)
# --------------------------------------------------------------------------- #
def test_debug_canonical_200_with_valid_token(presence_with_token):
    r = client.get("/debug/canonical", headers={"Authorization": f"Bearer {VALID}"})
    assert r.status_code == 200


def test_models_200_with_valid_token(presence_with_token):
    r = client.get("/v1/models", headers={"Authorization": f"Bearer {VALID}"})
    assert r.status_code == 200


# --------------------------------------------------------------------------- #
# /health ouvert ; console non gardée par le token
# --------------------------------------------------------------------------- #
def test_health_open_even_with_auth_enforced(presence_with_token):
    assert client.get("/health").status_code == 200


def test_console_not_gated_by_api_token(presence_with_token):
    # Console montée par défaut et NON protégée par le token API (UI navigateur).
    r = client.get("/console", follow_redirects=True)
    assert r.status_code == 200


# --------------------------------------------------------------------------- #
# Modes off / presence-sans-token : auth non appliquée
# --------------------------------------------------------------------------- #
def test_off_mode_allows_without_token(monkeypatch):
    monkeypatch.setenv("AAC_API_AUTH_MODE", "off")
    monkeypatch.setenv("AAC_API_TOKEN", VALID)  # off l'emporte même avec un token posé
    assert client.get("/debug/canonical").status_code == 200


def test_presence_without_token_allows(monkeypatch):
    monkeypatch.delenv("AAC_API_AUTH_MODE", raising=False)
    monkeypatch.delenv("AAC_API_TOKEN", raising=False)
    assert client.get("/debug/canonical").status_code == 200


# --------------------------------------------------------------------------- #
# Mode required : refus de démarrage sans token valide (fail-closed)
# --------------------------------------------------------------------------- #
def test_required_without_token_refuses_start(monkeypatch):
    monkeypatch.setenv("AAC_API_AUTH_MODE", "required")
    monkeypatch.delenv("AAC_API_TOKEN", raising=False)
    with pytest.raises(AuthConfigError):
        validate_startup_auth()


def test_required_with_short_token_refuses_start(monkeypatch):
    monkeypatch.setenv("AAC_API_AUTH_MODE", "required")
    monkeypatch.setenv("AAC_API_TOKEN", "short")
    with pytest.raises(AuthConfigError):
        validate_startup_auth()


def test_required_with_valid_token_starts(monkeypatch):
    monkeypatch.setenv("AAC_API_AUTH_MODE", "required")
    monkeypatch.setenv("AAC_API_TOKEN", VALID)
    validate_startup_auth()  # ne lève pas


def test_required_without_token_lifespan_refuses(monkeypatch):
    """Le garde-fou est bien câblé dans le lifespan de démarrage."""
    monkeypatch.setenv("AAC_API_AUTH_MODE", "required")
    monkeypatch.delenv("AAC_API_TOKEN", raising=False)
    app2 = create_app()
    with pytest.raises(AuthConfigError):
        with TestClient(app2):
            pass


def test_required_mode_enforces_token(monkeypatch):
    monkeypatch.setenv("AAC_API_AUTH_MODE", "required")
    monkeypatch.setenv("AAC_API_TOKEN", VALID)
    app2 = create_app()
    c = TestClient(app2)
    assert c.get("/debug/canonical").status_code == 401
    assert c.get("/debug/canonical", headers={"Authorization": f"Bearer {VALID}"}).status_code == 200


# --------------------------------------------------------------------------- #
# Profil exposé : /docs, /redoc, /openapi.json désactivés
# --------------------------------------------------------------------------- #
def test_docs_disabled_in_required_mode(monkeypatch):
    monkeypatch.setenv("AAC_API_AUTH_MODE", "required")
    monkeypatch.setenv("AAC_API_TOKEN", VALID)
    c = TestClient(create_app())
    assert c.get("/openapi.json").status_code == 404
    assert c.get("/docs").status_code == 404
    assert c.get("/redoc").status_code == 404


def test_docs_enabled_in_presence_mode(monkeypatch):
    monkeypatch.delenv("AAC_API_AUTH_MODE", raising=False)
    monkeypatch.delenv("AAC_API_TOKEN", raising=False)
    c = TestClient(create_app())
    assert c.get("/openapi.json").status_code == 200


# --------------------------------------------------------------------------- #
# Gating console via AAC_CONSOLE_ENABLED
# --------------------------------------------------------------------------- #
def test_console_disabled_when_flag_off(monkeypatch):
    monkeypatch.setenv("AAC_CONSOLE_ENABLED", "0")
    c = TestClient(create_app())
    assert c.get("/console", follow_redirects=True).status_code == 404


def test_console_enabled_by_default(monkeypatch):
    monkeypatch.delenv("AAC_CONSOLE_ENABLED", raising=False)
    c = TestClient(create_app())
    assert c.get("/console", follow_redirects=True).status_code == 200
