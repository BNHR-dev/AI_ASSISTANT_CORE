import importlib

from app.engine.visual_types import VisualRequest


def reload_comfyui_client():
    import app.clients.comfyui_client as comfyui_client
    return importlib.reload(comfyui_client)


def test_comfyui_payload_uses_env_checkpoint_and_not_legacy(monkeypatch):
    monkeypatch.setenv("COMFYUI_CHECKPOINT_NAME", "sd_xl_base_1.0.safetensors")
    monkeypatch.setenv("COMFYUI_DEFAULT_WORKFLOW", "cinematic_scene_v1")

    comfyui_client = reload_comfyui_client()

    request = VisualRequest(
        workflow_id="cinematic_scene_v1",
        positive_prompt="cyberpunk city at night",
        negative_prompt="blurry, low quality",
        seed=42,
        width=1024,
        height=1024,
        steps=30,
        cfg=7.0,
    )

    payload = comfyui_client.build_comfyui_prompt_payload(request)
    payload_str = str(payload)

    assert "sd_xl_base_1.0.safetensors" in payload_str
    assert "v1-5-pruned-emaonly.ckpt" not in payload_str