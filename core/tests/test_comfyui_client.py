from app.clients.comfyui_client import extract_output_file, inject_visual_request, load_workflow_template, enrich_visual_positive_prompt
from app.engine.visual_types import VisualRequest



def test_load_workflow_template_ok():
    workflow = load_workflow_template("cinematic_scene_v1")
    assert "3" in workflow
    assert "6" in workflow
    assert "9" in workflow



def test_inject_visual_request_updates_expected_nodes(monkeypatch):
    monkeypatch.setenv("COMFYUI_CHECKPOINT_NAME", "v1-5-pruned-emaonly.ckpt")

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
    )

    workflow = load_workflow_template("cinematic_scene_v1")
    injected = inject_visual_request(workflow, request)

    assert injected["6"]["inputs"]["text"] == "neon alley"
    assert injected["7"]["inputs"]["text"] == "blurry"
    assert injected["3"]["inputs"]["seed"] == 123
    assert injected["3"]["inputs"]["steps"] == 22
    assert injected["3"]["inputs"]["cfg"] == 6.5
    assert injected["5"]["inputs"]["width"] == 768
    assert injected["5"]["inputs"]["height"] == 512
    assert injected["9"]["inputs"]["filename_prefix"] == "cinematic_scene_v1"



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
