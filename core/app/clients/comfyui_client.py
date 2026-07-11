from __future__ import annotations

import copy
import json
import os
import random
import re
import time
import unicodedata
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests

from app.clients.comfyui_runtime import COMFYUI_URL, ensure_comfyui_runtime
from app.engine.visual_types import VisualIntentAnalysis, VisualRequest, VisualResult
from app.engine.visual_workflow_selector import analyze_visual_intent, select_visual_workflow


COMFYUI_OUTPUT_DIR = os.getenv("COMFYUI_OUTPUT_DIR", "")
COMFYUI_DEFAULT_WORKFLOW = os.getenv("COMFYUI_DEFAULT_WORKFLOW", "cinematic_scene_v1")
COMFYUI_CHECKPOINT_NAME = os.getenv("COMFYUI_CHECKPOINT_NAME", "sd_xl_base_1.0.safetensors")
# Default aligned with scripts/models.manifest (single source of truth) and the
# downloaded artifact. Docker/.env still override via COMFYUI_REFINER_CHECKPOINT_NAME.
COMFYUI_REFINER_CHECKPOINT_NAME = os.getenv(
    "COMFYUI_REFINER_CHECKPOINT_NAME", "RealVisXL_V5.0_fp16.safetensors"
)
COMFYUI_UPSCALE_MODEL_NAME = os.getenv("COMFYUI_UPSCALE_MODEL_NAME", "4x-UltraSharp.pth")
# Overall budget to wait for one render, and the per-poll HTTP timeout. The first render
# after a cold start loads a ~6.6 GB checkpoint and the single-threaded ComfyUI server is
# briefly unresponsive -> generous defaults + transient-tolerant polling (see
# wait_for_completion) so the very first image does not spuriously fail.
COMFYUI_HISTORY_TIMEOUT = int(os.getenv("COMFYUI_HISTORY_TIMEOUT", "300"))
COMFYUI_POLL_TIMEOUT = int(os.getenv("COMFYUI_POLL_TIMEOUT", "15"))
WORKFLOWS_DIR = Path(__file__).resolve().parents[1] / "workflows" / "comfyui"

# Per-quality graph templates. The category workflow_id (object/portrait/scene)
# still drives prompt enrichment, dimensions and the output prefix, but the
# actual ComfyUI graph that runs is selected by quality.
QUALITY_TEMPLATES = {
    "draft": "generic_draft_v1",
    "final": "generic_final_v1",
}

# Single source of truth for the node-level injection contract of each template.
# Centralising node IDs here avoids fragile, duplicated mutations across the code
# base. If a template no longer matches its contract, injection fails loudly.
WORKFLOW_CONTRACTS: dict[str, dict[str, Any]] = {
    "generic_draft_v1": {
        "base_ckpt_nodes": ["4"],
        "refiner_ckpt_nodes": [],
        "latent_node": "5",
        "positive_nodes": ["6"],
        "negative_nodes": ["7"],
        "seed_nodes": [("3", "seed"), ("11", "seed")],
        # The draft hires resample keeps a fixed, light step count (set in the
        # template) so iteration stays fast; only the base sampler uses request.steps.
        "steps_nodes": ["3"],
        "cfg_nodes": ["3", "11"],
        "save_node": "9",
        "upscale_model_nodes": [],
        "has_refiner": False,
    },
    "generic_final_v1": {
        "base_ckpt_nodes": ["4"],
        "refiner_ckpt_nodes": ["12"],
        "latent_node": "5",
        "positive_nodes": ["6", "15"],
        "negative_nodes": ["7", "16"],
        "seed_nodes": [("10", "noise_seed"), ("11", "noise_seed"), ("24", "seed")],
        "steps_nodes": ["10", "11", "24"],
        "cfg_nodes": ["10", "11", "24"],
        "save_node": "9",
        "upscale_model_nodes": ["20"],
        "has_refiner": True,
    },
}


def resolve_template_id(quality: str) -> str:
    if quality not in QUALITY_TEMPLATES:
        raise ComfyUIClientError(
            f"Unknown quality {quality!r}; expected one of {tuple(QUALITY_TEMPLATES)}"
        )
    return QUALITY_TEMPLATES[quality]


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
    quality: str = "draft",
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
        quality=quality,
    )


# Matches `--final` only as a standalone token (preceded by start/space, followed
# by end/space). Avoids false positives inside words like "finale" or "--finalize".
_QUALITY_FINAL_RE = re.compile(r"(?:(?<=\s)|^)--final(?=\s|$)")


def extract_quality_flag(prompt: str) -> tuple[str, str]:
    """
    Split a user prompt into (cleaned_prompt, quality).

    Presence of the standalone ``--final`` token selects quality="final" and the
    token is stripped from the prompt. Absence keeps the default quality="draft".
    """
    text = prompt or ""
    if _QUALITY_FINAL_RE.search(text):
        quality = "final"
        text = _QUALITY_FINAL_RE.sub(" ", text)
    else:
        quality = "draft"

    cleaned = " ".join(text.split())
    return cleaned, quality


def build_visual_request_from_text(prompt: str) -> VisualRequest:
    cleaned, quality = extract_quality_flag(prompt)
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
        quality=quality,
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
        quality=request.quality,
        output_subfolder=request.output_subfolder,
    )


def _require_node(
    workflow: dict[str, Any], node_id: str, template_id: str
) -> dict[str, Any]:
    node = workflow.get(node_id)
    if not isinstance(node, dict) or "inputs" not in node:
        raise WorkflowTemplateError(
            f"Workflow template '{template_id}' is missing required node '{node_id}' "
            "or its inputs; template no longer matches its injection contract."
        )
    return node["inputs"]


def _set_node_input(
    workflow: dict[str, Any], node_id: str, key: str, value: Any, template_id: str
) -> None:
    inputs = _require_node(workflow, node_id, template_id)
    if key not in inputs:
        raise WorkflowTemplateError(
            f"Workflow template '{template_id}' node '{node_id}' is missing input "
            f"'{key}'; template no longer matches its injection contract."
        )
    inputs[key] = value


def inject_visual_request(
    workflow: dict[str, Any], request: VisualRequest, template_id: str
) -> dict[str, Any]:
    contract = WORKFLOW_CONTRACTS.get(template_id)
    if contract is None:
        raise WorkflowTemplateError(f"No injection contract for template '{template_id}'")

    injected = copy.deepcopy(workflow)

    for node_id in contract["base_ckpt_nodes"]:
        _set_node_input(injected, node_id, "ckpt_name", COMFYUI_CHECKPOINT_NAME, template_id)
    for node_id in contract["refiner_ckpt_nodes"]:
        _set_node_input(
            injected, node_id, "ckpt_name", COMFYUI_REFINER_CHECKPOINT_NAME, template_id
        )
    for node_id in contract["upscale_model_nodes"]:
        _set_node_input(injected, node_id, "model_name", COMFYUI_UPSCALE_MODEL_NAME, template_id)

    _set_node_input(injected, contract["latent_node"], "width", request.width, template_id)
    _set_node_input(injected, contract["latent_node"], "height", request.height, template_id)

    for node_id in contract["positive_nodes"]:
        _set_node_input(injected, node_id, "text", request.positive_prompt, template_id)
    for node_id in contract["negative_nodes"]:
        _set_node_input(injected, node_id, "text", request.negative_prompt, template_id)

    for node_id, key in contract["seed_nodes"]:
        _set_node_input(injected, node_id, key, request.seed, template_id)
    for node_id in contract["steps_nodes"]:
        _set_node_input(injected, node_id, "steps", request.steps, template_id)
    for node_id in contract["cfg_nodes"]:
        _set_node_input(injected, node_id, "cfg", request.cfg, template_id)

    # Dossier par run : regroupe l'image et le manifest dans <output>/<output_subfolder>/.
    filename_prefix = (
        f"{request.output_subfolder}/{request.workflow_id}"
        if request.output_subfolder
        else request.workflow_id
    )
    _set_node_input(
        injected, contract["save_node"], "filename_prefix", filename_prefix, template_id
    )

    return injected


def build_comfyui_prompt_payload(request: VisualRequest) -> dict[str, Any]:
    template_id = resolve_template_id(request.quality)
    workflow = load_workflow_template(template_id)
    return inject_visual_request(workflow, request, template_id)


def get_comfyui_system_info() -> dict[str, Any] | None:
    """
    Versions du serveur ComfyUI (repro tier 1), via GET /system_stats.
    Best-effort : None si injoignable ou réponse inattendue — le manifest
    enregistre alors `comfyui: null` plutôt que de bloquer le run.
    """
    try:
        response = requests.get(f"{COMFYUI_URL}/system_stats", timeout=5)
        if not response.ok:
            return None
        system = response.json().get("system")
        if not isinstance(system, dict):
            return None
        return {
            "comfyui_version": system.get("comfyui_version"),
            "pytorch_version": system.get("pytorch_version"),
            "python_version": system.get("python_version"),
        }
    except (requests.RequestException, ValueError):
        return None


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


def wait_for_completion(prompt_id: str, timeout_seconds: int | None = None) -> dict[str, Any]:
    if timeout_seconds is None:
        timeout_seconds = COMFYUI_HISTORY_TIMEOUT

    deadline = time.time() + timeout_seconds
    last_history: dict[str, Any] | None = None
    last_transient: str | None = None

    while time.time() < deadline:
        try:
            response = requests.get(
                f"{COMFYUI_URL}/history/{prompt_id}", timeout=COMFYUI_POLL_TIMEOUT
            )
        except requests.RequestException as exc:
            # ComfyUI is single-threaded: while it loads a checkpoint or samples, the HTTP
            # server is briefly unresponsive. A transient poll timeout must NOT abort the
            # whole wait -> keep polling until the overall deadline.
            last_transient = str(exc)
            time.sleep(2)
            continue

        if response.ok:
            try:
                data = response.json()
            except ValueError:
                # Partial/garbled body under load: treat as transient, keep polling.
                time.sleep(1)
                continue

            if prompt_id in data:
                return data[prompt_id]

            last_history = data

        time.sleep(1)

    detail = f"Last history={last_history}"
    if last_transient:
        detail += f"; last transient error={last_transient}"
    raise ComfyUIClientError(
        f"ComfyUI history timeout for prompt_id={prompt_id} after {timeout_seconds}s. {detail}"
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


def _build_view_url(filename: str, subfolder: str, image_type: str) -> str:
    """
    Build a ComfyUI HTTP view URL for an image declared in the run history.

    The endpoint is the standard ComfyUI `/view` route, which serves the raw
    bytes of an output image identified by (filename, subfolder, type).
    This URL is reachable from anywhere that can reach the ComfyUI HTTP API
    (e.g. the local single-host endpoint 127.0.0.1:8188).
    """
    query = urlencode(
        {
            "filename": filename,
            "subfolder": subfolder or "",
            "type": image_type or "output",
        }
    )
    return f"{COMFYUI_URL}/view?{query}"


def extract_output_descriptors(history: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Return a list of image descriptors from a ComfyUI run history.

    Each descriptor is shaped as:
        {
            "filename": str,
            "subfolder": str,
            "type": str,        # usually "output"
            "view_url": str,    # absolute ComfyUI /view URL
        }

    Unlike extract_output_file(), this helper does NOT try to resolve a local
    filesystem path. It only exposes what ComfyUI itself reports plus the HTTP
    URL that any client (host or VM) can use to download the bytes.
    """
    descriptors: list[dict[str, Any]] = []
    outputs = history.get("outputs", {})

    for node_data in outputs.values():
        images = node_data.get("images", [])
        if not images:
            continue

        for image in images:
            if not isinstance(image, dict):
                continue

            filename = image.get("filename")
            if not filename:
                continue

            subfolder = image.get("subfolder") or ""
            image_type = image.get("type") or "output"

            descriptors.append(
                {
                    "filename": filename,
                    "subfolder": subfolder,
                    "type": image_type,
                    "view_url": _build_view_url(filename, subfolder, image_type),
                }
            )

    return descriptors


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
        quality=base_request.quality,
        output_subfolder=base_request.output_subfolder,
    )


def run_comfyui_workflow(request_or_prompt: str | VisualRequest) -> dict[str, Any]:
    request = _normalize_request(request_or_prompt)
    request = finalize_visual_request(request)

    # Backend non-root : pré-créer le dossier du run sur le volume partagé AVANT
    # que ComfyUI (conteneur root) ne le crée — sinon le dossier naît root et le
    # backend ne peut plus y écrire le manifest. Best-effort : en cas d'échec,
    # l'écriture du manifest loggue déjà la sienne (non bloquant).
    if COMFYUI_OUTPUT_DIR and request.output_subfolder:
        try:
            Path(COMFYUI_OUTPUT_DIR, request.output_subfolder).mkdir(
                parents=True, exist_ok=True
            )
        except OSError:
            pass

    ensure_comfyui_ready()

    print(
        "COMFYUI WORKFLOW:",
        request.workflow_id,
        f"(quality={request.quality}, template={resolve_template_id(request.quality)})",
    )
    print(
        "COMFYUI PARAMETERS:",
        {
            "workflow_id": request.workflow_id,
            "quality": request.quality,
            "template_id": resolve_template_id(request.quality),
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
            descriptors = extract_output_descriptors(history)
            first_descriptor = descriptors[0] if descriptors else {}

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
                        "subfolder": first_descriptor.get("subfolder"),
                        "type": first_descriptor.get("type"),
                        "view_url": first_descriptor.get("view_url"),
                        "history": history,
                        "seed": run_seed,
                        # Repro tier 1 : le workflow TEL QU'ENVOYÉ (pas reconstruit
                        # a posteriori — l'env peut changer entre-temps). Consommé
                        # par le manifest (sidecar workflow_resolved_v<i>.json).
                        "workflow_resolved": workflow,
                        "error": error_message,
                    }
                )
                continue

            results.append(
                {
                    "prompt_id": prompt_id,
                    "filename": filename,
                    "output_path": output_path,
                    "subfolder": first_descriptor.get("subfolder"),
                    "type": first_descriptor.get("type"),
                    "view_url": first_descriptor.get("view_url"),
                    "history": history,
                    "seed": run_seed,
                    "workflow_resolved": workflow,
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
                    "subfolder": None,
                    "type": None,
                    "view_url": None,
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
            "artifact_view_url": None,
            "artifact_view_urls": [],
        }

    first_result = valid_results[0]
    artifact_view_urls = [
        item.get("view_url")
        for item in valid_results
        if item.get("view_url")
    ]
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
        "artifact_view_url": first_result.get("view_url"),
        "artifact_view_urls": artifact_view_urls,
    }