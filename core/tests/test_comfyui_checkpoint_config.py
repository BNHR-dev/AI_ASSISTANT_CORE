import importlib


def reload_comfyui_client():
    import app.clients.comfyui_client as comfyui_client
    return importlib.reload(comfyui_client)


def test_comfyui_checkpoint_name_uses_env_override(monkeypatch):
    monkeypatch.setenv("COMFYUI_CHECKPOINT_NAME", "sd_xl_base_1.0.safetensors")

    module = reload_comfyui_client()

    assert module.COMFYUI_CHECKPOINT_NAME == "sd_xl_base_1.0.safetensors"


def test_comfyui_checkpoint_name_default_is_not_legacy(monkeypatch):
    monkeypatch.delenv("COMFYUI_CHECKPOINT_NAME", raising=False)

    module = reload_comfyui_client()

    assert module.COMFYUI_CHECKPOINT_NAME != "v1-5-pruned-emaonly.ckpt"
    assert module.COMFYUI_CHECKPOINT_NAME == "sd_xl_base_1.0.safetensors"