"""
Tests BYO Ollama (chantier 6) — instance configurable, rôles de modèles,
erreurs actionnables, santé, provenance.

Un FAUX serveur Ollama (http.server dans un thread, port éphémère) sert
/api/tags, /api/version et /api/generate : les tests exercent le VRAI
client HTTP et les vraies sondes — aucun mock de la couche requests.

Invariants couverts :
- défauts inchangés : sans env posée, les modèles historiques et le
  timeout 240 s restent exactement ceux d'avant le chantier ;
- rôles : AAC_OLLAMA_GENERAL/CODER/VISION_MODEL remplacent les défauts du
  routage au point de sortie unique (enrich_route_config — chemins auto
  et forcé) ; un modèle hors mapping passe inchangé ;
- Blender : AAC_BLENDER_LLM_MODEL > AAC_OLLAMA_CODER_MODEL > défaut ;
- embedding : source unique AAC_EMBED_MODEL (ollama_runtime, ré-exporté
  par router_embeddings) ;
- client : succès contre le faux serveur ; instance injoignable →
  RuntimeError avec l'endpoint et le nom d'env à vérifier ; modèle absent
  (404) → hint `ollama pull` ;
- santé : reachable ≠ ready, modèles de génération requis manquants
  listés, embedding manquant = optionnel signalé sans casser ready ;
- provenance : bloc ollama (endpoint + version + modèles par rôle) dans
  les sections repro des manifests.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.infra import ollama_runtime as orun

DEFAULT_MODELS = (
    "qwen3:8b",
    "qwen2.5-coder:7b",
    "qwen2.5vl:3b",
    "bge-m3:latest",
)

_OLLAMA_ENV_VARS = (
    "OLLAMA_BASE_URL",
    "OLLAMA_URL",
    "OLLAMA_GENERATE_URL",
    "OLLAMA_TAGS_URL",
    orun.OLLAMA_TIMEOUT_ENV,
    orun.GENERAL_MODEL_ENV,
    orun.CODER_MODEL_ENV,
    orun.VISION_MODEL_ENV,
    orun.EMBED_MODEL_ENV,
    "AAC_BLENDER_LLM_MODEL",
)


class FakeOllama:
    """Serveur HTTP minimal parlant le sous-ensemble d'API utilisé par AAC."""

    def __init__(self, models: tuple[str, ...] = DEFAULT_MODELS, version: str = "0.99.0"):
        self.models = list(models)
        self.version = version
        self.generate_calls: list[dict] = []
        state = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # silence des logs de test
                pass

            def _send(self, code: int, payload: dict) -> None:
                body = json.dumps(payload).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path == "/api/tags":
                    self._send(200, {"models": [{"name": n} for n in state.models]})
                elif self.path == "/api/version":
                    self._send(200, {"version": state.version})
                else:
                    self._send(404, {"error": "not found"})

            def do_POST(self):
                if self.path != "/api/generate":
                    self._send(404, {"error": "not found"})
                    return
                length = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(length) or b"{}")
                state.generate_calls.append(payload)
                model = payload.get("model")
                if model not in state.models:
                    self._send(404, {"error": f"model '{model}' not found"})
                    return
                self._send(200, {"response": f"echo:{model}"})

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        self.base_url = f"http://127.0.0.1:{self._server.server_address[1]}"

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """Neutralise l'environnement Ollama ambiant (dev local, .env)."""
    for var in _OLLAMA_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


@pytest.fixture
def fake_ollama(clean_env: pytest.MonkeyPatch):
    server = FakeOllama()
    clean_env.setenv("OLLAMA_BASE_URL", server.base_url)
    yield server
    server.stop()


# ---------------------------------------------------------------------------
# Rôles de modèles et timeout — défauts inchangés
# ---------------------------------------------------------------------------

def test_defaults_without_env_are_historical(clean_env) -> None:
    assert orun.resolve_role_model("general") == "qwen3:8b"
    assert orun.resolve_role_model("coder") == "qwen2.5-coder:7b"
    assert orun.resolve_role_model("vision") == "qwen2.5vl:3b"
    assert orun.get_embed_model() == "bge-m3"
    assert orun.get_ollama_timeout() == 240.0


def test_apply_model_override_maps_roles(clean_env) -> None:
    clean_env.setenv(orun.GENERAL_MODEL_ENV, "llama3.3:70b")
    clean_env.setenv(orun.CODER_MODEL_ENV, "deepseek-coder-v2:16b")
    assert orun.apply_model_override("qwen3:8b") == "llama3.3:70b"
    assert orun.apply_model_override("qwen2.5-coder:7b") == "deepseek-coder-v2:16b"
    assert orun.apply_model_override("qwen2.5vl:3b") == "qwen2.5vl:3b"  # env absente
    # Hors mapping : passe inchangé (pas de magie).
    assert orun.apply_model_override("mistral:7b") == "mistral:7b"
    assert orun.apply_model_override(None) is None


@pytest.mark.parametrize("raw,expected", [("30", 30.0), ("0.2", 1.0), ("abc", 240.0), ("", 240.0)])
def test_timeout_env_parsing(clean_env, raw: str, expected: float) -> None:
    clean_env.setenv(orun.OLLAMA_TIMEOUT_ENV, raw)
    assert orun.get_ollama_timeout() == expected


def test_base_url_derived_from_generate_url(clean_env) -> None:
    clean_env.setenv("OLLAMA_BASE_URL", "http://192.168.1.50:11434/")
    assert orun.get_ollama_base_url() == "http://192.168.1.50:11434"


# ---------------------------------------------------------------------------
# Routage : le remplacement s'applique au point de sortie unique
# ---------------------------------------------------------------------------

def test_enrich_route_config_applies_override(clean_env) -> None:
    from app.engine.routing_conditions import enrich_route_config

    clean_env.setenv(orun.GENERAL_MODEL_ENV, "llama3.3:70b")
    enriched = enrich_route_config(
        "explain_basic", "explique les décorateurs",
        {"task_type": "explain_basic", "selected_model": "qwen3:8b"},
    )
    assert enriched["selected_model"] == "llama3.3:70b"


def test_forced_mode_path_applies_override(clean_env) -> None:
    from app.engine.executor import _build_forced_mode_decision

    clean_env.setenv(orun.CODER_MODEL_ENV, "deepseek-coder-v2:16b")
    decision = _build_forced_mode_decision("écris une fonction", "build")
    assert decision["selected_model"] == "deepseek-coder-v2:16b"


def test_route_decision_unchanged_without_env(clean_env) -> None:
    from app.engine.executor import _build_forced_mode_decision

    decision = _build_forced_mode_decision("écris une fonction", "build")
    assert decision["selected_model"] == "qwen2.5-coder:7b"


# ---------------------------------------------------------------------------
# Blender : ordre de résolution spécifique > rôle coder > défaut
# ---------------------------------------------------------------------------

def test_blender_model_resolution_order(clean_env) -> None:
    from app.engine.blender_model_config import get_blender_llm_model

    assert get_blender_llm_model() == "qwen2.5-coder:7b"
    clean_env.setenv(orun.CODER_MODEL_ENV, "deepseek-coder-v2:16b")
    assert get_blender_llm_model() == "deepseek-coder-v2:16b"
    clean_env.setenv("AAC_BLENDER_LLM_MODEL", "qwen2.5-coder:14b")
    assert get_blender_llm_model() == "qwen2.5-coder:14b"  # le spécifique gagne


def test_embed_model_single_source(clean_env) -> None:
    from app.engine import router_embeddings as remb

    clean_env.setenv(orun.EMBED_MODEL_ENV, "nomic-embed-text")
    assert remb.get_embed_model() == "nomic-embed-text"
    assert remb.get_embed_model is orun.get_embed_model  # délégation, pas copie


def test_configured_generation_models(clean_env) -> None:
    clean_env.setenv(orun.GENERAL_MODEL_ENV, "llama3.3:70b")
    models = orun.configured_generation_models()
    assert "llama3.3:70b" in models
    assert "qwen3:8b" not in models  # remplacé partout où il était le défaut
    assert "qwen2.5-coder:7b" in models  # coder non remplacé (routage + Blender)
    assert len(models) == len(set(models))  # uniques


# ---------------------------------------------------------------------------
# Santé : reachable ≠ ready, modèles requis, embedding optionnel
# ---------------------------------------------------------------------------

def test_ollama_status_ready_with_all_models(fake_ollama: FakeOllama) -> None:
    from app.infra.tool_manager import get_ollama_status

    status = get_ollama_status()
    assert status["reachable"] is True
    assert status["ready"] is True
    assert status["missing"] == []
    assert "modeles requis presents" in status["reason"]
    # bge-m3 (requis sans tag) est matché par bge-m3:latest installé.
    assert "embedding" not in status["reason"]


def test_ollama_status_missing_generation_model(clean_env) -> None:
    from app.infra.tool_manager import get_ollama_status

    server = FakeOllama(models=("qwen3:8b", "qwen2.5vl:3b", "bge-m3:latest"))
    clean_env.setenv("OLLAMA_BASE_URL", server.base_url)
    try:
        status = get_ollama_status()
    finally:
        server.stop()

    assert status["reachable"] is True
    assert status["ready"] is False  # répond mais ne peut pas servir le routage
    assert status["missing"] == ["qwen2.5-coder:7b"]
    assert "qwen2.5-coder:7b" in status["reason"]


def test_ollama_status_missing_embed_stays_ready(clean_env) -> None:
    from app.infra.tool_manager import get_ollama_status

    server = FakeOllama(models=("qwen3:8b", "qwen2.5-coder:7b", "qwen2.5vl:3b"))
    clean_env.setenv("OLLAMA_BASE_URL", server.base_url)
    try:
        status = get_ollama_status()
    finally:
        server.stop()

    assert status["ready"] is True  # la couche embeddings se dégrade proprement
    assert status["missing"] == []
    assert "embedding optionnel absent: bge-m3" in status["reason"]


def test_ollama_status_unreachable(clean_env) -> None:
    from app.infra.tool_manager import get_ollama_status

    clean_env.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:9")
    status = get_ollama_status()
    assert status == {
        "reachable": False, "ready": False,
        "reason": status["reason"], "missing": [],
    }
    assert status["reason"]  # l'erreur transport est remontée telle quelle


def test_runtime_health_exposes_ollama_models(clean_env, monkeypatch) -> None:
    """/health/runtime : endpoint = base URL, modèles manquants listés,
    statut global degraded (Ollama est requis)."""
    from app.engine import runtime_debug as rd

    server = FakeOllama(models=("qwen3:8b", "bge-m3:latest"))
    clean_env.setenv("OLLAMA_BASE_URL", server.base_url)
    monkeypatch.setattr(rd, "is_searxng_ready", lambda: (True, "http 200"))
    monkeypatch.setattr(
        rd,
        "get_comfyui_status",
        lambda: {"reachable": True, "ready": True, "reason": "ok", "missing": []},
    )
    try:
        health = rd.get_runtime_health()
    finally:
        server.stop()

    ollama = health["services"]["ollama"]
    assert ollama["endpoint"] == server.base_url
    assert ollama["reachable"] is True and ollama["ready"] is False
    assert "qwen2.5-coder:7b" in ollama["missing"]
    assert "qwen2.5vl:3b" in ollama["missing"]
    assert health["status"] == "degraded"


# ---------------------------------------------------------------------------
# Provenance : bloc ollama dans les sections repro des manifests
# ---------------------------------------------------------------------------

def test_ollama_environment_capture(fake_ollama: FakeOllama, clean_env) -> None:
    from app.engine import repro

    clean_env.setenv(orun.GENERAL_MODEL_ENV, "llama3.3:70b")
    repro.reset_probe_memos()
    env = repro.ollama_environment()

    assert env["base_url"] == fake_ollama.base_url
    assert env["version"] == "0.99.0"
    assert env["models"]["general"] == "llama3.3:70b"
    assert set(env["models"]) == {"general", "coder", "vision", "blender", "embedding"}

    # La version est mémoïsée par endpoint : serveur coupé → même réponse.
    fake_ollama.stop()
    assert repro.ollama_environment()["version"] == "0.99.0"


def test_ollama_environment_unreachable_is_none(clean_env) -> None:
    from app.engine import repro

    clean_env.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:9")
    repro.reset_probe_memos()
    env = repro.ollama_environment()
    assert env["version"] is None  # best-effort, jamais d'exception
    assert env["models"]["general"] == "qwen3:8b"


def test_manifest_repro_sections_carry_ollama(clean_env, monkeypatch) -> None:
    from app.engine import repro
    from app.engine.artifact_manifest import _repro_section as blender_section
    from app.engine.comfyui_manifest import _repro_section as comfyui_section

    sentinel = {"base_url": "http://x:1", "version": "9.9.9", "models": {}}
    monkeypatch.setattr(repro, "ollama_environment", lambda: sentinel)

    blender_block = blender_section(None, None, {})
    comfyui_block = comfyui_section({}, None)
    assert blender_block["ollama"] == sentinel
    assert comfyui_block["ollama"] == sentinel


def test_console_provenance_badge_shows_ollama() -> None:
    import console

    view = console._provenance_view(
        {"repro": {"ollama": {"base_url": "http://192.168.1.50:11434",
                              "version": "0.9.0", "models": {}}}},
        run_dir=__import__("pathlib").Path("/nonexistent"),
        run_id="req-1",
    )
    labels = {badge["label"]: badge["value"] for badge in view["badges"]}
    assert labels["ollama"] == "v0.9.0 · http://192.168.1.50:11434"


# ---------------------------------------------------------------------------
# Client : succès et erreurs actionnables contre un vrai serveur HTTP
# ---------------------------------------------------------------------------

def test_generate_against_fake_server(fake_ollama: FakeOllama) -> None:
    from app.clients.ollama_client import generate_with_ollama

    result = generate_with_ollama("qwen3:8b", "bonjour")
    assert result == "echo:qwen3:8b"
    assert fake_ollama.generate_calls[0]["model"] == "qwen3:8b"
    assert fake_ollama.generate_calls[0]["stream"] is False


def test_generate_missing_model_hints_pull(fake_ollama: FakeOllama) -> None:
    from app.clients.ollama_client import generate_with_ollama

    with pytest.raises(RuntimeError) as exc_info:
        generate_with_ollama("gpt-oss:20b", "bonjour")
    message = str(exc_info.value)
    assert "Ollama error 404" in message
    assert "ollama pull gpt-oss:20b" in message
    assert fake_ollama.base_url in message  # l'endpoint fautif est nommé


def test_generate_unreachable_is_actionable(clean_env) -> None:
    from app.clients.ollama_client import generate_with_ollama

    clean_env.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:9")  # port réservé, fermé
    with pytest.raises(RuntimeError) as exc_info:
        generate_with_ollama("qwen3:8b", "bonjour")
    message = str(exc_info.value)
    assert "unreachable" in message
    assert "http://127.0.0.1:9" in message
    assert "OLLAMA_BASE_URL" in message  # quoi vérifier, pas juste « refused »
