"""wait_for_completion must survive transient ComfyUI unresponsiveness.

The first render after a cold start loads a ~6.6 GB checkpoint; ComfyUI's single-threaded
server then times out individual /history polls. A transient poll error must NOT abort the
whole wait (the bug that made the very first post-install image fail).
"""
import pytest

import app.clients.comfyui_client as cc


def test_tolerates_transient_poll_errors_then_completes(monkeypatch):
    monkeypatch.setattr(cc.time, "sleep", lambda *a, **k: None)
    state = {"n": 0}

    class _Resp:
        ok = True

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    def _get(url, timeout=15):
        state["n"] += 1
        if state["n"] <= 3:
            # first polls time out while the checkpoint loads
            raise cc.requests.RequestException("read timed out (busy loading checkpoint)")
        return _Resp({"PID42": {"outputs": {"9": {"images": [{"filename": "out.png"}]}}}})

    monkeypatch.setattr(cc.requests, "get", _get)

    result = cc.wait_for_completion("PID42", timeout_seconds=30)
    assert result["outputs"]["9"]["images"][0]["filename"] == "out.png"
    assert state["n"] >= 4  # it kept polling past the transient errors


def test_raises_after_overall_deadline(monkeypatch):
    monkeypatch.setattr(cc.time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(
        cc.requests,
        "get",
        lambda url, timeout=15: (_ for _ in ()).throw(cc.requests.RequestException("always busy")),
    )
    with pytest.raises(cc.ComfyUIClientError):
        cc.wait_for_completion("PID", timeout_seconds=0)


def test_default_timeout_comes_from_env_constant():
    # Default budget is the module constant (configurable via COMFYUI_HISTORY_TIMEOUT).
    assert cc.COMFYUI_HISTORY_TIMEOUT >= 120
