from __future__ import annotations

import time
import uuid
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


def format_openai_response(model: str, content: str) -> dict[str, Any]:
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
    assistant_text = normalize_execute_output(result)
    return format_openai_response(payload.model, assistant_text)