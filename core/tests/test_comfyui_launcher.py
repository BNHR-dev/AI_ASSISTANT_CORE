from app.infra.comfyui_launcher import ensure_comfyui_ready


def test_ensure_comfyui_ready_returns_tuple(monkeypatch):
    monkeypatch.setattr("app.infra.comfyui_launcher.is_comfyui_running", lambda: True)
    ok, reason = ensure_comfyui_ready()
    assert ok is True
    assert "already running" in reason.lower()
