from __future__ import annotations

import time
import uuid
import base64
import mimetypes
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.engine.executor import execute_request


router = APIRouter(prefix="/v1", tags=["openai-compatible"])

MODEL_TO_MODE = {
    "assistant-core-auto": "auto",
    "assistant-core-prof": "explain",
    "assistant-core-builder": "build",
    "assistant-core-archi": "architecture",
    "assistant-core-exam": "critique",
    "assistant-core-vision": "vision",
    "assistant-core-image": "image_generation",
    "assistant-core-web": "web_research",
}

DEFAULT_VISION_PROMPT = "Analyse cette image."

MAX_EMBED_IMAGES = 4
MAX_EMBED_BYTES_PER_IMAGE = 4 * 1024 * 1024  # 4 MiB
_PROJECT_ROOT = Path(__file__).resolve().parent

class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"] = "user"
    content: Any = ""


class ChatCompletionRequest(BaseModel):
    model: str = Field(default="assistant-core-auto")
    messages: list[ChatMessage]
    stream: bool = False
    temperature: float | None = None
    max_tokens: int | None = None


class ModelCard(BaseModel):
    id: str
    object: str = "model"
    owned_by: str = "ai_assistant_core"


class ModelsResponse(BaseModel):
    object: str = "list"
    data: list[ModelCard]


class ExtractedUserTurn(BaseModel):
    text: str
    has_image: bool = False


def _normalize_message_content_to_text(content: Any) -> str:
    if content is None:
        return ""

    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):
        text_parts: list[str] = []

        for item in content:
            if isinstance(item, str):
                stripped = item.strip()
                if stripped:
                    text_parts.append(stripped)
                continue

            if not isinstance(item, dict):
                continue

            part_type = str(item.get("type", "")).lower()
            if part_type not in {"text", "input_text"}:
                continue

            text_value = item.get("text")
            if isinstance(text_value, str) and text_value.strip():
                text_parts.append(text_value.strip())

        return "\n".join(text_parts).strip()

    if isinstance(content, dict):
        text_value = content.get("text")
        if isinstance(text_value, str):
            return text_value.strip()

    return str(content).strip()


def _message_has_image_content(content: Any) -> bool:
    if isinstance(content, list):
        for item in content:
            if not isinstance(item, dict):
                continue

            part_type = str(item.get("type", "")).lower()
            if part_type in {"image_url", "input_image", "image"}:
                return True

    return False


def extract_last_user_turn(messages: list[ChatMessage]) -> ExtractedUserTurn:
    for msg in reversed(messages):
        if msg.role != "user":
            continue

        text = _normalize_message_content_to_text(msg.content)
        has_image = _message_has_image_content(msg.content)

        if text:
            return ExtractedUserTurn(text=text, has_image=has_image)

        if has_image:
            return ExtractedUserTurn(
                text=DEFAULT_VISION_PROMPT,
                has_image=True,
            )

    raise HTTPException(
        status_code=400,
        detail="No usable user message found in request.",
    )


def format_openai_response(
    model: str,
    content: str | list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": content,
                },
                "finish_reason": "stop",
            }
        ],
    }


def normalize_execute_output(result: Any) -> str:
    if result is None:
        return "No response produced."

    if isinstance(result, str):
        return result

    if isinstance(result, dict):
        for key in ["response", "output", "final_output", "final_answer", "answer"]:
            if key in result and isinstance(result[key], str):
                return result[key]

        return str(result)

    return str(result)

def _resolve_artifact_path(raw_path: str) -> Path:
    candidate = Path(raw_path)
    if candidate.is_absolute():
        return candidate.resolve()
    return (_PROJECT_ROOT / candidate).resolve()


def _read_image_as_data_uri(path: Path) -> str | None:
    mime, _ = mimetypes.guess_type(str(path))
    if mime is None or not mime.startswith("image/"):
        mime = "image/png"
    try:
        raw_bytes = path.read_bytes()
    except OSError:
        return None
    encoded = base64.b64encode(raw_bytes).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _collect_artifact_paths(result: dict[str, Any]) -> list[str]:
    raw = result.get("artifact_paths")
    if isinstance(raw, list) and raw:
        return [p for p in raw if isinstance(p, str) and p]
    single = result.get("artifact_path")
    if isinstance(single, str) and single:
        return [single]
    return []


def _build_visual_response_content(result: Any) -> str | list[dict[str, Any]]:
    fallback_text = normalize_execute_output(result)

    if not isinstance(result, dict):
        return fallback_text
    if result.get("artifact_type") != "image":
        return fallback_text

    raw_paths = _collect_artifact_paths(result)
    if not raw_paths:
        return fallback_text

    image_parts: list[dict[str, Any]] = []
    oversized = 0
    missing = 0
    read_errors = 0

    for raw in raw_paths:
        if len(image_parts) >= MAX_EMBED_IMAGES:
            break

        resolved = _resolve_artifact_path(raw)
        if not resolved.is_file():
            missing += 1
            continue

        try:
            size = resolved.stat().st_size
        except OSError:
            missing += 1
            continue

        if size > MAX_EMBED_BYTES_PER_IMAGE:
            oversized += 1
            continue

        data_uri = _read_image_as_data_uri(resolved)
        if data_uri is None:
            read_errors += 1
            continue

        image_parts.append(
            {"type": "image_url", "image_url": {"url": data_uri}}
        )

    if not image_parts:
        if oversized and not missing and not read_errors:
            reason = "taille supérieure à la limite d'intégration"
        elif missing and not oversized and not read_errors:
            reason = "fichier introuvable"
        else:
            reason = "non intégrable"
        mention = f"[Image générée mais non intégrée à la réponse : {reason}.]"
        if fallback_text:
            return f"{fallback_text}\n\n{mention}"
        return mention

    parts: list[dict[str, Any]] = []
    if fallback_text:
        parts.append({"type": "text", "text": fallback_text})
    parts.extend(image_parts)
    return parts


def _assemble_assistant_content(result: Any) -> str | list[dict[str, Any]]:
    if isinstance(result, dict):
        return _build_visual_response_content(result)
    return normalize_execute_output(result)

@router.get("/models", response_model=ModelsResponse)
def list_models() -> ModelsResponse:
    return ModelsResponse(
        data=[ModelCard(id=model_id) for model_id in MODEL_TO_MODE]
    )


@router.post("/chat/completions")
def chat_completions(payload: ChatCompletionRequest) -> dict[str, Any]:
    user_turn = extract_last_user_turn(payload.messages)
    system_mode = MODEL_TO_MODE.get(payload.model, "auto")

    roles_summary = [msg.role for msg in payload.messages]
    print(
        "[OPENAI_COMPAT] "
        f"model={payload.model} "
        f"resolved_mode={system_mode} "
        f"has_image={user_turn.has_image} "
        f"messages={len(payload.messages)} "
        f"roles={roles_summary} "
        f"user_message={user_turn.text[:200]}"
    )

    result = execute_request(
        user_turn.text,
        user_turn.has_image,
        mode=system_mode,
    )
    assistant_content = _assemble_assistant_content(result)
    return format_openai_response(payload.model, assistant_content)
