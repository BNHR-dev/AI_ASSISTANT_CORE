"""
Phase 4 — embed via ComfyUI HTTP /view.

These tests pin the new branch in `_build_visual_response_content`:
when the executor exposes `artifact_view_url` / `artifact_view_urls`,
openai_compat must download the bytes from ComfyUI over HTTP and embed
them as a data URI, instead of trying to read a local filesystem path
that is structurally inaccessible from the backend VM.

The local-filesystem branch is covered by test_openai_compat_visual_embed.py
and remains the fallback for the host-only legacy profile.
"""
from __future__ import annotations

import base64

import requests

from openai_compat import (
    ChatCompletionRequest,
    ChatMessage,
    MAX_EMBED_BYTES_PER_IMAGE,
    MAX_EMBED_IMAGES,
    chat_completions,
)


# Minimal valid PNG (1x1 transparent), small enough to fit any byte budget.
_PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\rIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


class _FakeResponse:
    def __init__(self, *, status_code: int = 200, content: bytes = b"", content_type: str = "image/png"):
        self.status_code = status_code
        self.content = content
        self.headers = {"Content-Type": content_type}

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300


def _install_fake_get(monkeypatch, handler):
    """Replace requests.get inside openai_compat with a controllable handler."""
    calls = []

    def fake_get(url, timeout=None, **kwargs):
        calls.append({"url": url, "timeout": timeout})
        return handler(url, timeout)

    monkeypatch.setattr("openai_compat.requests.get", fake_get)
    return calls


def _run(monkeypatch, model, result):
    def fake_execute(message, has_image=False, mode="auto"):
        return result

    monkeypatch.setattr("openai_compat.execute_request", fake_execute)
    payload = ChatCompletionRequest(
        model=model,
        messages=[ChatMessage(role="user", content="go")],
    )
    return chat_completions(payload)


# ---------------------------------------------------------------------------
# Happy path: HTTP /view returns PNG bytes -> data URI is embedded
# ---------------------------------------------------------------------------

def test_single_view_url_embeds_data_uri(monkeypatch):
    calls = _install_fake_get(
        monkeypatch,
        lambda url, timeout: _FakeResponse(content=_PNG_BYTES),
    )

    response = _run(
        monkeypatch,
        "assistant-core-image",
        {
            "output": "image générée",
            "artifact_type": "image",
            "artifact_view_url": "http://192.168.77.1:8188/view?filename=a.png&subfolder=&type=output",
            # artifact_path is intentionally unset to prove we never touched the filesystem
        },
    )

    content = response["choices"][0]["message"]["content"]
    # Phase 5 contract: content is ALWAYS a string
    assert isinstance(content, str)
    assert content.startswith("image générée")
    assert "](data:image/png;base64," in content
    # round-trip: the embedded base64 decodes back to the exact PNG bytes
    prefix = "data:image/png;base64,"
    start = content.index(prefix) + len(prefix)
    end = content.index(")", start)
    assert base64.b64decode(content[start:end]) == _PNG_BYTES
    # alt text from the ?filename= query param
    assert "![a.png]" in content

    assert len(calls) == 1
    assert calls[0]["url"].startswith("http://192.168.77.1:8188/view?")
    assert calls[0]["timeout"] is not None and calls[0]["timeout"] > 0


def test_multiple_view_urls_embed_all_images(monkeypatch):
    _install_fake_get(
        monkeypatch,
        lambda url, timeout: _FakeResponse(content=_PNG_BYTES),
    )

    response = _run(
        monkeypatch,
        "assistant-core-image",
        {
            "output": "2 variantes",
            "artifact_type": "image",
            "artifact_view_urls": [
                "http://comfyui/view?filename=a.png&subfolder=&type=output",
                "http://comfyui/view?filename=b.png&subfolder=&type=output",
            ],
        },
    )

    content = response["choices"][0]["message"]["content"]
    assert isinstance(content, str)
    assert content.count("](data:image/png;base64,") == 2
    assert "![a.png]" in content
    assert "![b.png]" in content


def test_view_url_branch_takes_priority_over_local_path(monkeypatch, tmp_path):
    """
    If both view_url and artifact_path are present, the HTTP branch wins.
    The local file is never opened — proven by using a path that does not
    exist on disk; if the local branch ran, the fallback message would mention
    'fichier introuvable'.
    """
    _install_fake_get(
        monkeypatch,
        lambda url, timeout: _FakeResponse(content=_PNG_BYTES),
    )

    nonexistent = tmp_path / "definitely-not-here.png"
    response = _run(
        monkeypatch,
        "assistant-core-image",
        {
            "output": "image faite",
            "artifact_type": "image",
            "artifact_path": str(nonexistent),
            "artifact_view_url": "http://comfyui/view?filename=x.png&subfolder=&type=output",
        },
    )

    content = response["choices"][0]["message"]["content"]
    assert isinstance(content, str)
    assert "](data:image/png;base64," in content
    # Crucially: no local-branch error message should leak in
    assert "fichier introuvable" not in content


# ---------------------------------------------------------------------------
# Failure modes: graceful fallback to text, no crash
# ---------------------------------------------------------------------------

def test_http_404_falls_back_to_explicit_text(monkeypatch):
    _install_fake_get(
        monkeypatch,
        lambda url, timeout: _FakeResponse(status_code=404, content=b"not found"),
    )

    response = _run(
        monkeypatch,
        "assistant-core-image",
        {
            "output": "image faite",
            "artifact_type": "image",
            "artifact_view_url": "http://comfyui/view?filename=missing.png&subfolder=&type=output",
        },
    )

    content = response["choices"][0]["message"]["content"]
    assert isinstance(content, str)
    assert "image faite" in content
    assert "non récupérable depuis ComfyUI" in content


def test_http_timeout_falls_back_to_explicit_text(monkeypatch):
    def raise_timeout(url, timeout):
        raise requests.Timeout("simulated timeout")

    _install_fake_get(monkeypatch, raise_timeout)

    response = _run(
        monkeypatch,
        "assistant-core-image",
        {
            "output": "image faite",
            "artifact_type": "image",
            "artifact_view_url": "http://comfyui/view?filename=slow.png&subfolder=&type=output",
        },
    )

    content = response["choices"][0]["message"]["content"]
    assert isinstance(content, str)
    assert "image faite" in content
    assert "non récupérable depuis ComfyUI" in content


def test_http_connection_error_falls_back_to_explicit_text(monkeypatch):
    def raise_conn(url, timeout):
        raise requests.ConnectionError("simulated connection refused")

    _install_fake_get(monkeypatch, raise_conn)

    response = _run(
        monkeypatch,
        "assistant-core-image",
        {
            "output": "image faite",
            "artifact_type": "image",
            "artifact_view_url": "http://comfyui/view?filename=down.png&subfolder=&type=output",
        },
    )

    content = response["choices"][0]["message"]["content"]
    assert isinstance(content, str)
    assert "non récupérable depuis ComfyUI" in content


def test_http_oversized_falls_back_to_size_text(monkeypatch):
    big_payload = b"\x89PNG" + b"\x00" * (MAX_EMBED_BYTES_PER_IMAGE + 1024)
    _install_fake_get(
        monkeypatch,
        lambda url, timeout: _FakeResponse(content=big_payload),
    )

    response = _run(
        monkeypatch,
        "assistant-core-image",
        {
            "output": "image faite",
            "artifact_type": "image",
            "artifact_view_url": "http://comfyui/view?filename=big.png&subfolder=&type=output",
        },
    )

    content = response["choices"][0]["message"]["content"]
    assert isinstance(content, str)
    assert "taille supérieure à la limite d'intégration" in content


def test_max_embed_images_is_respected_via_http(monkeypatch):
    _install_fake_get(
        monkeypatch,
        lambda url, timeout: _FakeResponse(content=_PNG_BYTES),
    )

    urls = [
        f"http://comfyui/view?filename=v{i}.png&subfolder=&type=output"
        for i in range(MAX_EMBED_IMAGES + 2)
    ]
    response = _run(
        monkeypatch,
        "assistant-core-image",
        {
            "output": "beaucoup",
            "artifact_type": "image",
            "artifact_view_urls": urls,
        },
    )

    content = response["choices"][0]["message"]["content"]
    assert isinstance(content, str)
    assert content.count("](data:image/png;base64,") == MAX_EMBED_IMAGES


def test_no_artifact_type_skips_http_branch_entirely(monkeypatch):
    """If artifact_type is absent, we must not even attempt an HTTP fetch."""
    calls = _install_fake_get(
        monkeypatch,
        lambda url, timeout: _FakeResponse(content=_PNG_BYTES),
    )

    response = _run(
        monkeypatch,
        "assistant-core-image",
        {
            "output": "pas un visuel",
            "artifact_view_url": "http://comfyui/view?filename=x.png&subfolder=&type=output",
        },
    )

    assert response["choices"][0]["message"]["content"] == "pas un visuel"
    assert calls == []
