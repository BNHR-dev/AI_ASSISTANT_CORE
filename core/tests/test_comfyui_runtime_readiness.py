"""ComfyUI readiness contract: reachable != ready.

A ComfyUI that answers HTTP but lacks the configured checkpoint/upscaler must report
ready=False (the false-green that made the console show comfyui green while a render
failed with "RealVisXL_V5.0_fp16.safetensors not in []").
"""
import app.engine.runtime_debug as rd
import app.infra.tool_manager as tm

REQUIRED = {
    "checkpoints": {"RealVisXL_V5.0_fp16.safetensors"},
    "upscale_models": {"4x-UltraSharp.pth"},
}


def _mock_required(monkeypatch):
    monkeypatch.setattr(tm, "_comfyui_required_models", lambda: {k: set(v) for k, v in REQUIRED.items()})


def test_reachable_but_empty_is_not_ready(monkeypatch):
    monkeypatch.setattr(tm, "is_comfyui_ready", lambda: (True, "http 200"))
    monkeypatch.setattr(tm, "_comfyui_available_models", lambda *a, **k: {"checkpoints": set(), "upscale_models": set()})
    _mock_required(monkeypatch)

    s = tm.get_comfyui_status()
    assert s["reachable"] is True
    assert s["ready"] is False
    assert "RealVisXL_V5.0_fp16.safetensors" in s["missing"]
    assert "4x-UltraSharp.pth" in s["missing"]


def test_reachable_with_all_models_is_ready(monkeypatch):
    monkeypatch.setattr(tm, "is_comfyui_ready", lambda: (True, "http 200"))
    monkeypatch.setattr(
        tm,
        "_comfyui_available_models",
        lambda *a, **k: {
            "checkpoints": {"RealVisXL_V5.0_fp16.safetensors", "other.safetensors"},
            "upscale_models": {"4x-UltraSharp.pth"},
        },
    )
    _mock_required(monkeypatch)

    s = tm.get_comfyui_status()
    assert s["reachable"] is True
    assert s["ready"] is True
    assert s["missing"] == []


def test_unreachable_is_not_ready(monkeypatch):
    monkeypatch.setattr(tm, "is_comfyui_ready", lambda: (False, "connection refused"))
    s = tm.get_comfyui_status()
    assert s["reachable"] is False
    assert s["ready"] is False
    assert "connection refused" in s["reason"]


def test_object_info_unreadable_is_not_ready(monkeypatch):
    monkeypatch.setattr(tm, "is_comfyui_ready", lambda: (True, "http 200"))
    monkeypatch.setattr(tm, "_comfyui_available_models", lambda *a, **k: None)
    s = tm.get_comfyui_status()
    assert s["reachable"] is True
    assert s["ready"] is False


def test_required_models_introspect_object_info(monkeypatch):
    """_comfyui_available_models reads the ComfyUI /object_info contract shape."""
    calls = {}

    class _Resp:
        ok = True

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    def _fake_get(url, timeout=4.0):
        calls.setdefault("urls", []).append(url)
        if "CheckpointLoaderSimple" in url:
            # legacy shape: [[choices], {meta}]
            return _Resp({"CheckpointLoaderSimple": {"input": {"required": {"ckpt_name": [["a.safetensors", "b.safetensors"], {"tooltip": "x"}]}}}})
        # COMBO shape (real ComfyUI for UpscaleModelLoader): ["COMBO", {"options": [...]}]
        return _Resp({"UpscaleModelLoader": {"input": {"required": {"model_name": ["COMBO", {"multiselect": False, "options": ["4x-UltraSharp.pth"]}]}}}})

    monkeypatch.setattr(tm.requests, "get", _fake_get)
    avail = tm._comfyui_available_models()
    assert avail["checkpoints"] == {"a.safetensors", "b.safetensors"}
    assert avail["upscale_models"] == {"4x-UltraSharp.pth"}
    assert any("object_info/CheckpointLoaderSimple" in u for u in calls["urls"])


def test_extract_object_info_choices_handles_both_shapes():
    # legacy list-at-[0]
    assert tm._extract_object_info_choices([["a", "b"], {"tooltip": "x"}]) == ["a", "b"]
    # COMBO type with options dict (real UpscaleModelLoader shape)
    assert tm._extract_object_info_choices(["COMBO", {"multiselect": False, "options": ["4x-UltraSharp.pth"]}]) == ["4x-UltraSharp.pth"]
    # unknown / malformed -> None
    assert tm._extract_object_info_choices("nope") is None
    assert tm._extract_object_info_choices([]) is None
    assert tm._extract_object_info_choices(["COMBO", {"multiselect": False}]) is None


def test_runtime_health_reports_comfyui_degraded_not_green(monkeypatch):
    # ollama/searxng are bound INTO runtime_debug at import -> patch them there.
    monkeypatch.setattr(
        rd,
        "get_ollama_status",
        lambda: {"reachable": True, "ready": True, "reason": "http 200", "missing": []},
    )
    monkeypatch.setattr(rd, "is_searxng_ready", lambda: (True, "http 200"))
    # comfyui status flows through the real get_comfyui_status -> patch the tool_manager leaves.
    monkeypatch.setattr(tm, "is_comfyui_ready", lambda: (True, "http 200"))
    monkeypatch.setattr(tm, "_comfyui_available_models", lambda *a, **k: {"checkpoints": set(), "upscale_models": set()})
    _mock_required(monkeypatch)

    health = rd.get_runtime_health()
    comfy = health["services"]["comfyui"]
    assert comfy["reachable"] is True
    assert comfy["ready"] is False          # NOT green
    assert comfy["missing"]                 # tells you exactly what is absent
    assert health["status"] == "partial"    # core ready, visual degraded -> not "ok"
