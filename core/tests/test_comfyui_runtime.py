def test_ensure_runtime_does_not_restart_if_ready(monkeypatch):
    monkeypatch.setattr("app.clients.comfyui_runtime.ping_comfyui", lambda timeout=4: True)

    result = __import__("app.clients.comfyui_runtime", fromlist=["ensure_comfyui_runtime"]).ensure_comfyui_runtime()
    assert result["ready"] is True
    assert result["started"] is False


def test_ensure_runtime_starts_if_needed(monkeypatch):
    calls = {"start": 0, "wait": 0}
    responses = iter([False])

    monkeypatch.setattr("app.clients.comfyui_runtime.COMFYUI_AUTO_START", True)
    monkeypatch.setattr("app.clients.comfyui_runtime.ping_comfyui", lambda timeout=4: next(responses, False))
    monkeypatch.setattr(
        "app.clients.comfyui_runtime.start_comfyui_process",
        lambda: calls.__setitem__("start", calls["start"] + 1) or type("P", (), {"pid": 1234})(),
    )
    monkeypatch.setattr(
        "app.clients.comfyui_runtime.wait_for_comfyui_ready",
        lambda timeout_seconds=None: calls.__setitem__("wait", calls["wait"] + 1),
    )

    result = __import__("app.clients.comfyui_runtime", fromlist=["ensure_comfyui_runtime"]).ensure_comfyui_runtime()
    assert result["started"] is True
    assert calls["start"] == 1
    assert calls["wait"] == 1
