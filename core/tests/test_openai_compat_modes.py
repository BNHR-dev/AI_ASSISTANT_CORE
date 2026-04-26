from fastapi import HTTPException

from openai_compat import (
    ChatCompletionRequest,
    ChatMessage,
    chat_completions,
    extract_last_user_turn,
)


def test_openai_compat_auto_mode(monkeypatch):
    captured = {}

    def fake_execute(message: str, has_image: bool = False, mode: str = "auto"):
        captured["message"] = message
        captured["mode"] = mode
        captured["has_image"] = has_image
        return {"output": "AUTO_OUTPUT"}

    monkeypatch.setattr("openai_compat.execute_request", fake_execute)

    payload = ChatCompletionRequest(
        model="assistant-core-auto",
        messages=[ChatMessage(role="user", content="explique moi les embeddings")],
    )

    response = chat_completions(payload)

    assert captured["mode"] == "auto"
    assert captured["has_image"] is False
    assert response["choices"][0]["message"]["content"] == "AUTO_OUTPUT"


def test_openai_compat_builder_mode(monkeypatch):
    captured = {}

    def fake_execute(message: str, has_image: bool = False, mode: str = "auto"):
        captured["mode"] = mode
        return {"output": "BUILD_OUTPUT"}

    monkeypatch.setattr("openai_compat.execute_request", fake_execute)

    payload = ChatCompletionRequest(
        model="assistant-core-builder",
        messages=[ChatMessage(role="user", content="écris un script python")],
    )

    response = chat_completions(payload)

    assert captured["mode"] == "build"
    assert response["choices"][0]["message"]["content"] == "BUILD_OUTPUT"


def test_extract_last_user_turn_uses_last_user_message_only():
    turn = extract_last_user_turn(
        [
            ChatMessage(role="system", content="ignore"),
            ChatMessage(role="user", content="ancien prompt"),
            ChatMessage(role="assistant", content="ancienne réponse"),
            ChatMessage(role="user", content="dernier prompt utile"),
        ]
    )

    assert turn.text == "dernier prompt utile"
    assert turn.has_image is False


def test_extract_last_user_turn_supports_multimodal_text_parts():
    turn = extract_last_user_turn(
        [
            ChatMessage(
                role="user",
                content=[
                    {"type": "text", "text": "cherche moi les dernières news IA"},
                    {"type": "image_url", "image_url": {"url": "https://example.com/a.png"}},
                ],
            )
        ]
    )

    assert turn.text == "cherche moi les dernières news IA"
    assert turn.has_image is True


def test_extract_last_user_turn_uses_default_prompt_for_image_only_message():
    turn = extract_last_user_turn(
        [
            ChatMessage(
                role="user",
                content=[
                    {"type": "image_url", "image_url": {"url": "https://example.com/a.png"}},
                ],
            )
        ]
    )

    assert turn.text == "Analyse cette image."
    assert turn.has_image is True


def test_openai_compat_passes_image_flag(monkeypatch):
    captured = {}

    def fake_execute(message: str, has_image: bool = False, mode: str = "auto"):
        captured["message"] = message
        captured["has_image"] = has_image
        captured["mode"] = mode
        return {"output": "VISION_OUTPUT"}

    monkeypatch.setattr("openai_compat.execute_request", fake_execute)

    payload = ChatCompletionRequest(
        model="assistant-core-auto",
        messages=[
            ChatMessage(role="assistant", content="ancienne réponse"),
            ChatMessage(
                role="user",
                content=[
                    {"type": "text", "text": "décris cette image"},
                    {"type": "image_url", "image_url": {"url": "https://example.com/a.png"}},
                ],
            ),
        ],
    )

    response = chat_completions(payload)

    assert captured == {
        "message": "décris cette image",
        "has_image": True,
        "mode": "auto",
    }
    assert response["choices"][0]["message"]["content"] == "VISION_OUTPUT"


def test_openai_compat_unknown_model_falls_back_to_auto(monkeypatch):
    captured = {}

    def fake_execute(message: str, has_image: bool = False, mode: str = "auto"):
        captured["mode"] = mode
        return {"output": "UNKNOWN_MODEL_OUTPUT"}

    monkeypatch.setattr("openai_compat.execute_request", fake_execute)

    payload = ChatCompletionRequest(
        model="assistant-core-something-else",
        messages=[ChatMessage(role="user", content="hello")],
    )

    response = chat_completions(payload)

    assert captured["mode"] == "auto"
    assert response["choices"][0]["message"]["content"] == "UNKNOWN_MODEL_OUTPUT"


def test_extract_last_user_turn_raises_when_no_usable_user_message():
    try:
        extract_last_user_turn(
            [
                ChatMessage(role="system", content="ignore"),
                ChatMessage(role="assistant", content="still ignore"),
            ]
        )
    except HTTPException as exc:
        assert exc.status_code == 400
        assert "No usable user message" in exc.detail
    else:
        raise AssertionError("Expected HTTPException when no usable user message exists.")


def test_openai_compat_vision_mode(monkeypatch):
    captured = {}

    def fake_execute(message: str, has_image: bool = False, mode: str = "auto"):
        captured["mode"] = mode
        captured["has_image"] = has_image
        return {"output": "VISION_OUTPUT"}

    monkeypatch.setattr("openai_compat.execute_request", fake_execute)

    payload = ChatCompletionRequest(
        model="assistant-core-vision",
        messages=[
            ChatMessage(
                role="user",
                content=[
                    {"type": "text", "text": "décris cette image"},
                    {"type": "image_url", "image_url": {"url": "https://example.com/a.png"}},
                ],
            )
        ],
    )

    response = chat_completions(payload)

    assert captured["mode"] == "vision"
    assert captured["has_image"] is True
    assert response["choices"][0]["message"]["content"] == "VISION_OUTPUT"


def test_openai_compat_image_generation_mode(monkeypatch):
    captured = {}

    def fake_execute(message: str, has_image: bool = False, mode: str = "auto"):
        captured["mode"] = mode
        captured["message"] = message
        return {"output": "IMAGE_OUTPUT"}

    monkeypatch.setattr("openai_compat.execute_request", fake_execute)

    payload = ChatCompletionRequest(
        model="assistant-core-image",
        messages=[ChatMessage(role="user", content="génère une scène cyberpunk")],
    )

    response = chat_completions(payload)

    assert captured["mode"] == "image_generation"
    assert captured["message"] == "génère une scène cyberpunk"
    assert response["choices"][0]["message"]["content"] == "IMAGE_OUTPUT"


def test_openai_compat_web_research_mode(monkeypatch):
    captured = {}

    def fake_execute(message: str, has_image: bool = False, mode: str = "auto"):
        captured["mode"] = mode
        return {"output": "WEB_OUTPUT"}

    monkeypatch.setattr("openai_compat.execute_request", fake_execute)

    payload = ChatCompletionRequest(
        model="assistant-core-web",
        messages=[ChatMessage(role="user", content="dernières avancées en IA")],
    )

    response = chat_completions(payload)

    assert captured["mode"] == "web_research"
    assert response["choices"][0]["message"]["content"] == "WEB_OUTPUT"


# GAP H — prof / archi / exam modes non testés explicitement
def test_openai_compat_prof_mode(monkeypatch):
    captured = {}

    def fake_execute(message: str, has_image: bool = False, mode: str = "auto"):
        captured["mode"] = mode
        return {"output": "PROF_OUTPUT"}

    monkeypatch.setattr("openai_compat.execute_request", fake_execute)

    payload = ChatCompletionRequest(
        model="assistant-core-prof",
        messages=[ChatMessage(role="user", content="explique les transformers")],
    )

    response = chat_completions(payload)

    assert captured["mode"] == "explain"
    assert response["choices"][0]["message"]["content"] == "PROF_OUTPUT"


def test_openai_compat_archi_mode(monkeypatch):
    captured = {}

    def fake_execute(message: str, has_image: bool = False, mode: str = "auto"):
        captured["mode"] = mode
        return {"output": "ARCHI_OUTPUT"}

    monkeypatch.setattr("openai_compat.execute_request", fake_execute)

    payload = ChatCompletionRequest(
        model="assistant-core-archi",
        messages=[ChatMessage(role="user", content="décris l'architecture MVC")],
    )

    response = chat_completions(payload)

    assert captured["mode"] == "architecture"
    assert response["choices"][0]["message"]["content"] == "ARCHI_OUTPUT"


def test_openai_compat_exam_mode(monkeypatch):
    captured = {}

    def fake_execute(message: str, has_image: bool = False, mode: str = "auto"):
        captured["mode"] = mode
        return {"output": "EXAM_OUTPUT"}

    monkeypatch.setattr("openai_compat.execute_request", fake_execute)

    payload = ChatCompletionRequest(
        model="assistant-core-exam",
        messages=[ChatMessage(role="user", content="critique ce code python")],
    )

    response = chat_completions(payload)

    assert captured["mode"] == "critique"
    assert response["choices"][0]["message"]["content"] == "EXAM_OUTPUT"


# GAP G — type input_text dans le contenu multimodal
def test_extract_last_user_turn_supports_input_text_type():
    turn = extract_last_user_turn(
        [
            ChatMessage(
                role="user",
                content=[
                    {"type": "input_text", "text": "requête via input_text"},
                ],
            )
        ]
    )

    assert turn.text == "requête via input_text"
    assert turn.has_image is False
