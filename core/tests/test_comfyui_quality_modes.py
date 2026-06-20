"""Tests for the per-request draft/final quality modes."""
import importlib

import pytest

from app.engine.visual_types import VisualRequest


def reload_client(monkeypatch=None, **env):
    import app.clients.comfyui_client as comfyui_client

    if monkeypatch is not None:
        for key, value in env.items():
            monkeypatch.setenv(key, value)
    return importlib.reload(comfyui_client)


# --- parsing of the --final flag -------------------------------------------------

def test_no_flag_is_draft():
    from app.clients.comfyui_client import extract_quality_flag

    cleaned, quality = extract_quality_flag("a cinematic portrait")
    assert quality == "draft"
    assert cleaned == "a cinematic portrait"


def test_final_flag_sets_final_and_is_stripped():
    from app.clients.comfyui_client import extract_quality_flag

    cleaned, quality = extract_quality_flag("a cinematic portrait --final")
    assert quality == "final"
    assert cleaned == "a cinematic portrait"


def test_final_flag_stripped_when_in_the_middle():
    from app.clients.comfyui_client import extract_quality_flag

    cleaned, quality = extract_quality_flag("a cinematic --final portrait")
    assert quality == "final"
    assert cleaned == "a cinematic portrait"


@pytest.mark.parametrize(
    "prompt",
    [
        "a grand finale on stage",        # substring inside a word
        "please --finalize the render",   # longer token, must not match
        "make it final",                  # bare word, no dashes
        "the final-cut look",             # hyphenated word
    ],
)
def test_false_positives_stay_draft(prompt):
    from app.clients.comfyui_client import extract_quality_flag

    cleaned, quality = extract_quality_flag(prompt)
    assert quality == "draft"
    assert "--final" not in cleaned or "finalize" in cleaned


def test_build_visual_request_from_text_draft_vs_final():
    from app.clients.comfyui_client import build_visual_request_from_text

    draft = build_visual_request_from_text("a cinematic portrait")
    final = build_visual_request_from_text("a cinematic portrait --final")

    assert draft.quality == "draft"
    assert final.quality == "final"
    # the flag must not leak into the prompt sent to ComfyUI
    assert "--final" not in final.positive_prompt
    # category selection still works and is unaffected by the flag
    assert draft.workflow_id == final.workflow_id == "portrait_basic_v1"


# --- quality validation ----------------------------------------------------------

def test_invalid_quality_is_rejected():
    with pytest.raises(ValueError):
        VisualRequest(workflow_id="portrait_basic_v1", positive_prompt="x", quality="hero")


# --- payload selection by quality ------------------------------------------------

def test_draft_payload_has_hires_and_no_refiner(monkeypatch):
    client = reload_client(
        monkeypatch, COMFYUI_CHECKPOINT_NAME="realvisxlV50_v50Bakedvae.safetensors"
    )
    request = VisualRequest(
        workflow_id="cinematic_scene_v1", positive_prompt="neon city", quality="draft"
    )
    payload = client.build_comfyui_prompt_payload(request)

    class_types = {node["class_type"] for node in payload.values()}
    # RealVisXL base is present
    assert any(
        n.get("inputs", {}).get("ckpt_name") == "realvisxlV50_v50Bakedvae.safetensors"
        for n in payload.values()
    )
    # hires pass is present (latent upscale)
    assert "LatentUpscaleBy" in class_types
    # NO refiner stage in draft: a single checkpoint loader, no advanced sampler
    assert sum(1 for n in payload.values() if n["class_type"] == "CheckpointLoaderSimple") == 1
    assert "KSamplerAdvanced" not in class_types
    assert "UpscaleModelLoader" not in class_types


def test_final_payload_has_refiner_and_esrgan(monkeypatch):
    client = reload_client(
        monkeypatch,
        COMFYUI_CHECKPOINT_NAME="realvisxlV50_v50Bakedvae.safetensors",
        COMFYUI_REFINER_CHECKPOINT_NAME="realvisxlV50_v50Bakedvae.safetensors",
        COMFYUI_UPSCALE_MODEL_NAME="4x-UltraSharp.pth",
    )
    request = VisualRequest(
        workflow_id="cinematic_scene_v1", positive_prompt="neon city", quality="final"
    )
    payload = client.build_comfyui_prompt_payload(request)

    class_types = {node["class_type"] for node in payload.values()}
    # base + refiner two-stage
    assert sum(1 for n in payload.values() if n["class_type"] == "CheckpointLoaderSimple") == 2
    assert "KSamplerAdvanced" in class_types
    # ESRGAN hires pass
    assert "UpscaleModelLoader" in class_types
    assert payload["20"]["inputs"]["model_name"] == "4x-UltraSharp.pth"
    assert payload["12"]["inputs"]["ckpt_name"] == "realvisxlV50_v50Bakedvae.safetensors"


def test_seed_and_params_injected_in_all_samplers(monkeypatch):
    client = reload_client(monkeypatch)
    request = VisualRequest(
        workflow_id="object_basic_v1",
        positive_prompt="a watch",
        seed=777,
        steps=24,
        cfg=6.0,
        quality="final",
    )
    payload = client.build_comfyui_prompt_payload(request)
    assert payload["10"]["inputs"]["noise_seed"] == 777
    assert payload["11"]["inputs"]["noise_seed"] == 777
    assert payload["24"]["inputs"]["seed"] == 777
    assert payload["10"]["inputs"]["steps"] == 24
    assert payload["24"]["inputs"]["cfg"] == 6.0


def test_no_persistent_mutation_between_draft_and_final(monkeypatch):
    """A final request must not contaminate the template reused by a later draft."""
    client = reload_client(monkeypatch)

    final_req = VisualRequest(
        workflow_id="cinematic_scene_v1", positive_prompt="A", seed=1, quality="final"
    )
    draft_req = VisualRequest(
        workflow_id="cinematic_scene_v1", positive_prompt="B", seed=2, quality="draft"
    )

    client.build_comfyui_prompt_payload(final_req)
    draft_payload = client.build_comfyui_prompt_payload(draft_req)

    # the on-disk template is untouched and reloads clean
    fresh = client.load_workflow_template("generic_draft_v1")
    assert fresh["6"]["inputs"]["text"] == "concept image"
    assert fresh["3"]["inputs"]["seed"] == 42
    # the draft payload reflects only the draft request
    assert draft_payload["6"]["inputs"]["text"] == "B"
    assert draft_payload["3"]["inputs"]["seed"] == 2


def test_missing_contract_node_fails_loudly(monkeypatch):
    client = reload_client(monkeypatch)
    broken = client.load_workflow_template("generic_draft_v1")
    del broken["11"]  # remove the hires resample node
    request = VisualRequest(workflow_id="cinematic_scene_v1", positive_prompt="x", quality="draft")
    with pytest.raises(client.WorkflowTemplateError):
        client.inject_visual_request(broken, request, "generic_draft_v1")
