from app.clients.comfyui_client import (
    enrich_visual_positive_prompt,
    extract_output_descriptors,
    extract_output_file,
    inject_visual_request,
    load_workflow_template,
)
from app.engine.visual_types import VisualRequest



def test_load_workflow_template_ok():
    workflow = load_workflow_template("cinematic_scene_v1")
    assert "3" in workflow
    assert "6" in workflow
    assert "9" in workflow



def test_inject_visual_request_updates_expected_nodes(monkeypatch):
    import importlib

    import app.clients.comfyui_client as comfyui_client

    monkeypatch.setenv("COMFYUI_CHECKPOINT_NAME", "realvisxlV50_v50Bakedvae.safetensors")
    comfyui_client = importlib.reload(comfyui_client)

    request = VisualRequest(
        workflow_id="cinematic_scene_v1",
        positive_prompt="neon alley",
        negative_prompt="blurry",
        seed=123,
        width=768,
        height=512,
        steps=22,
        cfg=6.5,
        variants_count=2,
        quality="draft",
    )

    workflow = comfyui_client.load_workflow_template("generic_draft_v1")
    injected = comfyui_client.inject_visual_request(workflow, request, "generic_draft_v1")

    # base sampler + prompts + dimensions
    assert injected["6"]["inputs"]["text"] == "neon alley"
    assert injected["7"]["inputs"]["text"] == "blurry"
    assert injected["3"]["inputs"]["seed"] == 123
    assert injected["3"]["inputs"]["steps"] == 22
    assert injected["3"]["inputs"]["cfg"] == 6.5
    assert injected["5"]["inputs"]["width"] == 768
    assert injected["5"]["inputs"]["height"] == 512
    # the hires resample pass shares the seed/cfg, but keeps its fixed light
    # step count (template-defined) so draft iteration stays fast
    assert injected["11"]["inputs"]["seed"] == 123
    assert injected["11"]["inputs"]["cfg"] == 6.5
    assert injected["11"]["inputs"]["steps"] == 15
    # category workflow_id is preserved as the output prefix
    assert injected["9"]["inputs"]["filename_prefix"] == "cinematic_scene_v1"
    assert injected["4"]["inputs"]["ckpt_name"] == "realvisxlV50_v50Bakedvae.safetensors"



def test_extract_output_file_ok():
    history = {
        "outputs": {
            "9": {
                "images": [
                    {
                        "filename": "image.png",
                        "subfolder": "phase2",
                        "type": "output",
                    }
                ]
            }
        }
    }

    filename, output_path = extract_output_file(history)

    assert filename == "image.png"
    assert output_path.replace("\\", "/").endswith("phase2/image.png")



def test_enrich_visual_positive_prompt_adds_poster_signals():
    enriched = enrich_visual_positive_prompt("je veux 2 propositions d'affiche sci-fi", "cinematic_scene_v1")
    assert "poster design" in enriched
    assert "key visual" in enriched


def test_enrich_visual_positive_prompt_adds_cyberpunk_portrait_signals():
    enriched = enrich_visual_positive_prompt("portrait cyberpunk sous la pluie", "portrait_basic_v1")
    assert "neon reflections" in enriched
    assert "subtle cybernetic details" in enriched


def test_extract_output_descriptors_single_image(monkeypatch):
    monkeypatch.setattr("app.clients.comfyui_client.COMFYUI_URL", "http://127.0.0.1:8188")

    history = {
        "outputs": {
            "9": {
                "images": [
                    {
                        "filename": "cinematic_scene_v1_00023_.png",
                        "subfolder": "",
                        "type": "output",
                    }
                ]
            }
        }
    }

    descriptors = extract_output_descriptors(history)

    assert len(descriptors) == 1
    desc = descriptors[0]
    assert desc["filename"] == "cinematic_scene_v1_00023_.png"
    assert desc["subfolder"] == ""
    assert desc["type"] == "output"
    assert desc["view_url"] == (
        "http://127.0.0.1:8188/view"
        "?filename=cinematic_scene_v1_00023_.png&subfolder=&type=output"
    )


def test_extract_output_descriptors_with_subfolder(monkeypatch):
    monkeypatch.setattr("app.clients.comfyui_client.COMFYUI_URL", "http://comfyui.local:8188")

    history = {
        "outputs": {
            "9": {
                "images": [
                    {
                        "filename": "image.png",
                        "subfolder": "phase4/run_a",
                        "type": "output",
                    }
                ]
            }
        }
    }

    descriptors = extract_output_descriptors(history)

    assert len(descriptors) == 1
    url = descriptors[0]["view_url"]
    # urlencode escapes the slash in the subfolder; ComfyUI accepts both forms.
    assert url.startswith("http://comfyui.local:8188/view?")
    assert "filename=image.png" in url
    assert "subfolder=phase4%2Frun_a" in url
    assert "type=output" in url


def test_extract_output_descriptors_multiple_images_one_node(monkeypatch):
    monkeypatch.setattr("app.clients.comfyui_client.COMFYUI_URL", "http://host:8188")

    history = {
        "outputs": {
            "9": {
                "images": [
                    {"filename": "a.png", "subfolder": "", "type": "output"},
                    {"filename": "b.png", "subfolder": "", "type": "output"},
                ]
            }
        }
    }

    descriptors = extract_output_descriptors(history)

    assert [d["filename"] for d in descriptors] == ["a.png", "b.png"]
    assert all(d["view_url"].startswith("http://host:8188/view?") for d in descriptors)


def test_extract_output_descriptors_skips_entries_without_filename(monkeypatch):
    monkeypatch.setattr("app.clients.comfyui_client.COMFYUI_URL", "http://host:8188")

    history = {
        "outputs": {
            "9": {
                "images": [
                    {"filename": "", "subfolder": "", "type": "output"},
                    {"filename": "ok.png", "subfolder": "", "type": "output"},
                ]
            }
        }
    }

    descriptors = extract_output_descriptors(history)

    assert len(descriptors) == 1
    assert descriptors[0]["filename"] == "ok.png"


def test_extract_output_descriptors_empty_history():
    assert extract_output_descriptors({}) == []
    assert extract_output_descriptors({"outputs": {}}) == []
    assert extract_output_descriptors({"outputs": {"9": {"images": []}}}) == []
