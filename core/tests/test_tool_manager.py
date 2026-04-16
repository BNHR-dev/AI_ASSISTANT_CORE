import importlib

from app.infra.tool_manager import ensure_llm_backend_ready, ensure_tool_ready


def test_ensure_tool_ready_none():
    result = ensure_tool_ready(None)
    assert result["ready"] is True
    assert result["tool"] is None


def test_ensure_llm_backend_ready_shape(monkeypatch):
    monkeypatch.setattr(
        "app.infra.tool_manager.is_ollama_ready",
        lambda: (True, "http 200"),
    )
    result = ensure_llm_backend_ready()
    assert result["ready"] is True
    assert result["tool"] == "ollama"


def test_tool_manager_derives_tags_endpoint_from_generate_url(monkeypatch):
    monkeypatch.setenv("OLLAMA_URL", "http://localhost:12000/api/generate")
    monkeypatch.delenv("OLLAMA_TAGS_URL", raising=False)

    module = importlib.reload(__import__("app.infra.runtime_urls", fromlist=["get_ollama_tags_url"]))

    assert module.get_ollama_tags_url() == "http://localhost:12000/api/tags"


def test_tool_manager_respects_comfyui_auto_start_flag(monkeypatch):
    monkeypatch.setenv("COMFYUI_AUTO_START", "false")
    monkeypatch.setattr("app.infra.tool_manager.is_comfyui_ready", lambda: (False, "connection refused"))

    result = ensure_tool_ready("comfyui")

    assert result["ready"] is False
    assert result["action"] == "none"
    assert result["reason"] == "COMFYUI_AUTO_START disabled"
