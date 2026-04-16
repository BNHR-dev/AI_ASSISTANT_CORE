from __future__ import annotations

import copy
import json
import os
import random
import re
import unicodedata
from pathlib import Path
from typing import Any

import requests

from app.clients.comfyui_runtime import COMFYUI_URL, ensure_comfyui_runtime
from app.engine.visual_types import VisualIntentAnalysis, VisualRequest, VisualResult
from app.engine.visual_workflow_selector import analyze_visual_intent, select_visual_workflow


COMFYUI_OUTPUT_DIR = os.getenv("COMFYUI_OUTPUT_DIR", "")
COMFYUI_DEFAULT_WORKFLOW = os.getenv("COMFYUI_DEFAULT_WORKFLOW", "cinematic_scene_v1")
COMFYUI_CHECKPOINT_NAME = os.getenv("COMFYUI_CHECKPOINT_NAME", "sd_xl_base_1.0.safetensors")
WORKFLOWS_DIR = Path(__file__).resolve().parents[1] / "workflows" / "comfyui"


class ComfyUIClientError(RuntimeError):
    pass


class WorkflowTemplateError(ComfyUIClientError):
    pass


def ensure_comfyui_ready() -> None:
    try:
        ensure_comfyui_runtime()
    except Exception as exc:
        raise ComfyUIClientError(f"ComfyUI runtime unavailable: {exc}") from exc


def load_workflow_template(workflow_id: str) -> dict[str, Any]:
    workflow_path = WORKFLOWS_DIR / f"{workflow_id}.json"
    if not workflow_path.exists():
        raise WorkflowTemplateError(f"Workflow template not found: {workflow_path}")

    try:
        return json.loads(workflow_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise WorkflowTemplateError(f"Invalid workflow template JSON: {workflow_path}") from exc


def _normalize_variant_text(text: str) -> str:
    text = text.lower().strip()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("’", "'")
    text = re.sub(r"[-_/]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text


def _dedupe_prompt_parts(parts: list[str]) -> str:
    seen: set[str] = set()
    deduped: list[str] = []

    for part in parts:
        cleaned = " ".join(part.strip().split())
        if not cleaned:
            continue

        key = _normalize_variant_text(cleaned)
        if key in seen:
            continue

        seen.add(key)
        deduped.append(cleaned)

    return ", ".join(deduped)


def _subject_prompt_parts(subject_type: str) -> list[str]:
    if subject_type == "portrait":
        return [
            "cinematic portrait",
            "close-up",
            "ultra detailed face",
            "sharp eyes",
            "skin detail",
            "shallow depth of field",
            "dramatic lighting",
            "high quality",
        ]

    if subject_type == "product":
        return [
            "luxury product photography",
            "clean product composition",
            "clean composition",
            "studio lighting",
            "premium reflections",
            "ultra detailed surfaces",
            "sharp focus",
            "high quality",
        ]

    return [
        "cinematic scene",
        "cinematic composition",
        "ultra detailed",
        "high quality",
        "sharp focus",
        "professional composition",
        "dramatic lighting",
        "environment storytelling",
        "high detail scene",
    ]


def _render_prompt_parts(render_intent: str) -> list[str]:
    if render_intent == "poster":
        return [
            "poster design",
            "poster composition",
            "key visual",
            "hero framing",
            "centered hero composition",
            "negative space for title",
            "negative space for title typography",
            "high impact visual",
        ]

    if render_intent == "cover":
        return [
            "cover art composition",
            "striking central composition",
            "graphic visual impact",
        ]

    if render_intent == "key_visual":
        return [
            "key visual",
            "advertising campaign visual",
            "hero shot",
            "brand-ready composition",
            "premium commercial feel",
        ]

    if render_intent == "packshot":
        return [
            "studio packshot",
            "clean background",
            "product photography",
            "premium reflections",
        ]

    return []


def _style_prompt_parts(
    subject_type: str,
    style_flags: list[str],
    render_intent: str,
) -> list[str]:
    parts: list[str] = []

    if "cyberpunk" in style_flags:
        parts.append("cyberpunk aesthetic")
        if subject_type == "portrait":
            parts.extend(
                [
                    "neon reflections",
                    "futuristic styling",
                    "subtle cybernetic details",
                ]
            )
            if "rainy" in style_flags:
                parts.append("rainy night atmosphere")
        else:
            parts.extend(
                [
                    "neon sci-fi atmosphere",
                    "futuristic city lighting",
                    "techno details",
                ]
            )

    if "sci_fi" in style_flags:
        parts.append("sci-fi atmosphere")

    if "neon" in style_flags:
        parts.append("neon lighting")

    if "rainy" in style_flags:
        parts.extend(["wet surfaces", "rainy mood"])

    if "luxury" in style_flags:
        parts.append("luxury feel")

    if "studio" in style_flags:
        parts.append("studio lighting")

    if "cinematic" in style_flags:
        parts.append("cinematic lighting")

    if render_intent in {"poster", "cover", "key_visual"}:
        parts.append("visual storytelling")

    return parts


def enrich_visual_positive_prompt(
    prompt: str | None = None,
    workflow_id: str = "",
    analysis: VisualIntentAnalysis | None = None,
    user_prompt: str | None = None,
) -> str:
    source_prompt = user_prompt if user_prompt is not None else prompt
    cleaned = " ".join((source_prompt or "").strip().split())
    if not cleaned:
        cleaned = "image conceptuelle"

    analysis = analysis or analyze_visual_intent(cleaned)

    parts = [
        cleaned,
        *_subject_prompt_parts(analysis.subject_type),
        *_render_prompt_parts(analysis.render_intent),
        *_style_prompt_parts(
            analysis.subject_type,
            analysis.style_flags,
            analysis.render_intent,
        ),
    ]

    if workflow_id == "portrait_basic_v1":
        parts.append("portrait composition")
    elif workflow_id == "object_basic_v1":
        parts.append("product-centered framing")
    elif workflow_id == "cinematic_scene_v1":
        parts.append("cinematic color grading")

    return _dedupe_prompt_parts(parts)


def detect_variants_count(prompt: str) -> int:
    text = _normalize_variant_text(prompt)

    patterns_4 = ["4 variantes", "4 versions", "4 propositions", "4 outputs", "4 images"]
    patterns_2 = ["2 variantes", "2 versions", "2 propositions", "2 outputs", "2 images"]

    if any(pattern in text for pattern in patterns_4):
        return 4
    if any(pattern in text for pattern in patterns_2):
        return 2
    return 1


def _dimensions_for_analysis(analysis: VisualIntentAnalysis) -> tuple[int, int]:
    if analysis.render_intent in {"poster", "cover"}:
        return 832, 1216

    if analysis.subject_type == "scene" and analysis.render_intent == "standard":
        return 1216, 832

    if analysis.subject_type == "product" and analysis.render_intent == "packshot":
        return 1024, 1024

    return 1024, 1024


def build_visual_request(
    prompt: str,
    workflow_id: str,
    variants_count: int = 1,
    analysis: VisualIntentAnalysis | None = None,
) -> VisualRequest:
    cleaned = " ".join(prompt.strip().split())
    if not cleaned:
        cleaned = "image conceptuelle"

    analysis = analysis or analyze_visual_intent(cleaned)
    width, height = _dimensions_for_analysis(analysis)

    return VisualRequest(
        workflow_id=workflow_id,
        positive_prompt=enrich_visual_positive_prompt(
            cleaned,
            workflow_id,
            analysis=analysis,
        ),
        seed=random.randint(1, 2**32 - 1),
        width=width,
        height=height,
        variants_count=max(1, variants_count),
    )


def build_visual_request_from_text(prompt: str) -> VisualRequest:
    cleaned = " ".join(prompt.strip().split())
    if not cleaned:
        cleaned = "image conceptuelle"

    analysis = analyze_visual_intent(cleaned)
    workflow_id, _reason = select_visual_workflow(cleaned or COMFYUI_DEFAULT_WORKFLOW)
    variants_count = detect_variants_count(cleaned)
    width, height = _dimensions_for_analysis(analysis)

    return VisualRequest(
        workflow_id=workflow_id or analysis.workflow_id or COMFYUI_DEFAULT_WORKFLOW,
        positive_prompt=cleaned,
        seed=random.randint(1, 2**32 - 1),
        width=width,
        height=height,
        variants_count=variants_count,
    )


def _normalize_request(request_or_prompt: str | VisualRequest) -> VisualRequest:
    if isinstance(request_or_prompt, VisualRequest):
        return request_or_prompt
    if isinstance(request_or_prompt, str):
        return build_visual_request_from_text(request_or_prompt)
    raise TypeError(f"Unsupported visual request type: {type(request_or_prompt)!r}")


def finalize_visual_request(request: VisualRequest) -> VisualRequest:
    workflow_id = request.workflow_id or COMFYUI_DEFAULT_WORKFLOW

    seed = request.seed
    if not isinstance(seed, int) or seed <= 0:
        seed = random.randint(1, 2**32 - 1)

    analysis = analyze_visual_intent(request.positive_prompt)

    return VisualRequest(
        workflow_id=workflow_id,
        positive_prompt=enrich_visual_positive_prompt(
            request.positive_prompt,
            workflow_id,
            analysis=analysis,
        ),
        negative_prompt=request.negative_prompt,
        seed=seed,
        width=request.width,
        height=request.height,
        steps=request.steps,
        cfg=request.cfg,
        variants_count=request.variants_count,
    )


def inject_visual_request(workflow: dict[str, Any], request: VisualRequest) -> dict[str, Any]:
    injected = copy.deepcopy(workflow)

    try:
        injected["4"]["inputs"]["ckpt_name"] = COMFYUI_CHECKPOINT_NAME
        injected["5"]["inputs"]["width"] = request.width
        injected["5"]["inputs"]["height"] = request.height
        injected["6"]["inputs"]["text"] = request.positive_prompt
        injected["7"]["inputs"]["text"] = request.negative_prompt
        injected["3"]["inputs"]["seed"] = request.seed
        injected["3"]["inputs"]["steps"] = request.steps
        injected["3"]["inputs"]["cfg"] = request.cfg
        injected["9"]["inputs"]["filename_prefix"] = request.workflow_id
    except KeyError as exc:
        raise WorkflowTemplateError(
            f"Workflow template '{request.workflow_id}' missing expected node structure: {exc}"
        ) from exc

    return injected


def build_comfyui_prompt_payload(request: VisualRequest) -> dict[str, Any]:
    try:
        workflow = load_workflow_template(request.workflow_id)
    except WorkflowTemplateError:
        if request.workflow_id != COMFYUI_DEFAULT_WORKFLOW:
            workflow = load_workflow_template(COMFYUI_DEFAULT_WORKFLOW)
            request = VisualRequest(
                workflow_id=COMFYUI_DEFAULT_WORKFLOW,
                positive_prompt=request.positive_prompt,
                negative_prompt=request.negative_prompt,
                seed=request.seed,
                width=request.width,
                height=request.height,
                steps=request.steps,
                cfg=request.cfg,
                variants_count=request.variants_count,
            )
        else:
            raise

    return inject_visual_request(workflow, request)


def queue_prompt(workflow: dict[str, Any]) -> str:
    try:
        response = requests.post(
            f"{COMFYUI_URL}/prompt",
            json={"prompt": workflow},
            timeout=30,
        )
    except requests.RequestException as exc:
        raise ComfyUIClientError(f"ComfyUI prompt request failed: {exc}") from exc

    if not response.ok:
        raise ComfyUIClientError(f"ComfyUI prompt error {response.status_code}: {response.text}")

    try:
        data = response.json()
    except ValueError as exc:
        raise ComfyUIClientError("ComfyUI prompt response is not valid JSON") from exc

    prompt_id = data.get("prompt_id")
    if not prompt_id:
        raise ComfyUIClientError(f"ComfyUI returned no prompt_id: {data}")

    return prompt_id


def wait_for_completion(prompt_id: str, timeout_seconds: int = 120) -> dict[str, Any]:
    import time

    deadline = time.time() + timeout_seconds
    last_history: dict[str, Any] | None = None

    while time.time() < deadline:
        try:
            response = requests.get(f"{COMFYUI_URL}/history/{prompt_id}", timeout=10)
        except requests.RequestException as exc:
            raise ComfyUIClientError(f"ComfyUI history request failed: {exc}") from exc

        if response.ok:
            try:
                data = response.json()
            except ValueError as exc:
                raise ComfyUIClientError("ComfyUI history response is not valid JSON") from exc

            if prompt_id in data:
                return data[prompt_id]

            last_history = data

        time.sleep(1)

    raise ComfyUIClientError(
        f"ComfyUI history timeout for prompt_id={prompt_id}. Last history={last_history}"
    )


def extract_output_file(history: dict[str, Any]) -> tuple[str | None, str | None]:
    outputs = history.get("outputs", {})
    for node_data in outputs.values():
        images = node_data.get("images", [])
        if not images:
            continue

        image = images[0]
        filename = image.get("filename")
        subfolder = image.get("subfolder") or ""
        if not filename:
            continue

        if COMFYUI_OUTPUT_DIR:
            output_path = str(Path(COMFYUI_OUTPUT_DIR) / subfolder / filename)
        else:
            output_path = str(Path(subfolder) / filename) if subfolder else filename

        return filename, output_path

    return None, None


def extract_output_path(history: dict[str, Any]) -> str | None:
    _filename, output_path = extract_output_file(history)
    return output_path


def _build_variant_request(base_request: VisualRequest, seed: int) -> VisualRequest:
    return VisualRequest(
        workflow_id=base_request.workflow_id,
        positive_prompt=base_request.positive_prompt,
        negative_prompt=base_request.negative_prompt,
        seed=seed,
        width=base_request.width,
        height=base_request.height,
        steps=base_request.steps,
        cfg=base_request.cfg,
        variants_count=base_request.variants_count,
    )


def run_comfyui_workflow(request_or_prompt: str | VisualRequest) -> dict[str, Any]:
    request = _normalize_request(request_or_prompt)
    request = finalize_visual_request(request)

    ensure_comfyui_ready()

    print("COMFYUI WORKFLOW:", request.workflow_id)
    print(
        "COMFYUI PARAMETERS:",
        {
            "workflow_id": request.workflow_id,
            "positive_prompt": request.positive_prompt,
            "negative_prompt": request.negative_prompt,
            "seed": request.seed,
            "width": request.width,
            "height": request.height,
            "steps": request.steps,
            "cfg": request.cfg,
            "variants_count": request.variants_count,
        },
    )

    run_count = max(1, request.variants_count)
    results: list[dict[str, Any]] = []
    run_errors: list[str] = []
    used_seeds: set[int] = set()

    for idx in range(run_count):
        if run_count == 1 and idx == 0:
            run_seed = request.seed
        else:
            run_seed = random.randint(1, 2**32 - 1)
            while run_seed in used_seeds:
                run_seed = random.randint(1, 2**32 - 1)

        used_seeds.add(run_seed)
        variant_request = _build_variant_request(request, run_seed)

        try:
            workflow = build_comfyui_prompt_payload(variant_request)
            prompt_id = queue_prompt(workflow)
            history = wait_for_completion(prompt_id)
            filename, output_path = extract_output_file(history)

            if not output_path:
                error_message = (
                    f"ComfyUI completed for variant {idx + 1}/{run_count}, "
                    "but no usable output file was detected."
                )
                run_errors.append(error_message)
                results.append(
                    {
                        "prompt_id": prompt_id,
                        "filename": filename,
                        "output_path": output_path,
                        "history": history,
                        "seed": run_seed,
                        "error": error_message,
                    }
                )
                continue

            results.append(
                {
                    "prompt_id": prompt_id,
                    "filename": filename,
                    "output_path": output_path,
                    "history": history,
                    "seed": run_seed,
                }
            )
        except Exception as exc:
            error_message = f"variant {idx + 1}/{run_count} failed: {exc}"
            run_errors.append(error_message)
            results.append(
                {
                    "prompt_id": None,
                    "filename": None,
                    "output_path": None,
                    "history": None,
                    "seed": run_seed,
                    "error": error_message,
                }
            )

    valid_results = [item for item in results if item.get("output_path")]
    completed_variants = len(valid_results)
    partial = completed_variants not in (0, run_count)

    if not valid_results:
        error_message = (
            run_errors[0]
            if run_errors
            else "ComfyUI completed, but no usable output files were detected."
        )
        first_prompt_id = next(
            (item.get("prompt_id") for item in results if item.get("prompt_id")),
            None,
        )
        result = VisualResult(
            status="error",
            workflow_id=request.workflow_id,
            filename=None,
            output_path=None,
            parameters=request.to_dict(),
            raw_response={"runs": results},
            error=error_message,
            filenames=[],
            output_paths=[],
            variants_count=run_count,
            completed_variants=0,
            partial=False,
            run_errors=run_errors,
        )
        return {
            **result.to_dict(),
            "prompt_id": first_prompt_id,
            "variant_prompt_ids": [item.get("prompt_id") for item in results if item.get("prompt_id")],
            "variant_seeds": [item.get("seed") for item in results],
        }

    first_result = valid_results[0]
    result = VisualResult(
        status="success",
        workflow_id=request.workflow_id,
        filename=first_result.get("filename"),
        output_path=first_result.get("output_path"),
        parameters=request.to_dict(),
        raw_response={"runs": results},
        error=(run_errors[0] if partial and run_errors else None),
        filenames=[item.get("filename") for item in valid_results if item.get("filename")],
        output_paths=[item.get("output_path") for item in valid_results if item.get("output_path")],
        variants_count=run_count,
        completed_variants=completed_variants,
        partial=partial,
        run_errors=run_errors,
    )

    return {
        **result.to_dict(),
        "prompt_id": first_result.get("prompt_id"),
        "variant_prompt_ids": [item.get("prompt_id") for item in results if item.get("prompt_id")],
        "variant_seeds": [item.get("seed") for item in results],
    }