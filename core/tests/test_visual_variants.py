from app.clients.comfyui_client import build_visual_request_from_text, detect_variants_count, run_comfyui_workflow
from app.engine.visual_types import VisualRequest


def test_detect_variants_count_from_prompt():
    assert detect_variants_count("fais 4 variantes d'un portrait cyberpunk") == 4
    assert detect_variants_count("je veux 2 versions d'une affiche") == 2
    assert detect_variants_count("génère une image cyberpunk") == 1


def test_build_visual_request_from_text_sets_variants_count(monkeypatch):
    monkeypatch.setattr("app.clients.comfyui_client.random.randint", lambda a, b: 123456)
    request = build_visual_request_from_text("fais 4 variantes d'un portrait cyberpunk")
    assert request.variants_count == 4
    assert request.seed == 123456


def test_run_comfyui_workflow_returns_multiple_outputs(monkeypatch):
    seeds = iter([101, 202, 303, 404])
    prompt_ids = iter(["prompt_1", "prompt_2", "prompt_3", "prompt_4"])

    monkeypatch.setattr("app.clients.comfyui_client.ensure_comfyui_ready", lambda: None)
    monkeypatch.setattr("app.clients.comfyui_client.random.randint", lambda a, b: next(seeds))
    monkeypatch.setattr("app.clients.comfyui_client.build_comfyui_prompt_payload", lambda request: {"seed": request.seed})
    monkeypatch.setattr("app.clients.comfyui_client.queue_prompt", lambda workflow: next(prompt_ids))
    monkeypatch.setattr(
        "app.clients.comfyui_client.wait_for_completion",
        lambda prompt_id: {
            "outputs": {
                "9": {
                    "images": [
                        {
                            "filename": f"{prompt_id}.png",
                            "subfolder": "variants",
                            "type": "output",
                        }
                    ]
                }
            }
        },
    )

    result = run_comfyui_workflow(
        VisualRequest(
            workflow_id="cinematic_scene_v1",
            positive_prompt="portrait cyberpunk",
            seed=999,
            variants_count=4,
        )
    )

    assert result["variants_count"] == 4
    assert result["completed_variants"] == 4
    assert result["partial"] is False
    assert len(result["output_paths"]) == 4
    assert len(result["filenames"]) == 4
    assert result["variant_seeds"] == [101, 202, 303, 404]
    assert result["variant_prompt_ids"] == ["prompt_1", "prompt_2", "prompt_3", "prompt_4"]
