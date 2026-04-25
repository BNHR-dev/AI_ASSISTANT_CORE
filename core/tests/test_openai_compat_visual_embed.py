from __future__ import annotations

import base64
from pathlib import Path

from openai_compat import (
    ChatCompletionRequest,
    ChatMessage,
    MAX_EMBED_BYTES_PER_IMAGE,
    MAX_EMBED_IMAGES,
    chat_completions,
)


# Minimal PNG valide (1x1 transparent)
_PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\rIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _make_png(path: Path, padded_size: int | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if padded_size is None:
        path.write_bytes(_PNG_BYTES)
    else:
        # on se fiche de la validité PNG ici : on ne teste que la borne de taille
        path.write_bytes(b"\x89PNG" + b"\x00" * (padded_size - 4))
    return path


def _run(monkeypatch, model: str, result):
    def fake_execute(message, has_image=False, mode="auto"):
        return result

    monkeypatch.setattr("openai_compat.execute_request", fake_execute)
    payload = ChatCompletionRequest(
        model=model,
        messages=[ChatMessage(role="user", content="go")],
    )
    return chat_completions(payload)


def test_text_mode_stays_string(monkeypatch):
    response = _run(
        monkeypatch,
        "assistant-core-auto",
        {"output": "réponse texte simple"},
    )
    content = response["choices"][0]["message"]["content"]
    assert isinstance(content, str)
    assert content == "réponse texte simple"


def test_missing_artifact_type_falls_back_to_text(monkeypatch):
    response = _run(
        monkeypatch,
        "assistant-core-image",
        {"output": "pas d'artefact", "artifact_paths": ["outputs/foo.png"]},
    )
    content = response["choices"][0]["message"]["content"]
    assert isinstance(content, str)
    assert content == "pas d'artefact"


def test_single_artifact_path_returns_markdown_with_data_uri(monkeypatch, tmp_path):
    img = _make_png(tmp_path / "out.png")
    response = _run(
        monkeypatch,
        "assistant-core-image",
        {
            "output": "image générée",
            "artifact_type": "image",
            "artifact_path": str(img),
        },
    )
    content = response["choices"][0]["message"]["content"]
    # Phase 5: content is ALWAYS a string for OpenAI spec compliance
    assert isinstance(content, str)
    # narrative text preserved at the top
    assert content.startswith("image générée")
    # markdown image syntax with data URI
    assert "![" in content
    assert "](data:image/png;base64," in content
    # round-trip: the embedded base64 decodes back to the original bytes
    prefix = "data:image/png;base64,"
    start = content.index(prefix) + len(prefix)
    end = content.index(")", start)
    assert base64.b64decode(content[start:end]) == _PNG_BYTES


def test_multiple_artifact_paths_embed_all_images(monkeypatch, tmp_path):
    p1 = _make_png(tmp_path / "a.png")
    p2 = _make_png(tmp_path / "b.png")
    response = _run(
        monkeypatch,
        "assistant-core-image",
        {
            "output": "2 variantes",
            "artifact_type": "image",
            "artifact_paths": [str(p1), str(p2)],
        },
    )
    content = response["choices"][0]["message"]["content"]
    assert isinstance(content, str)
    # two markdown image blocks, one per artifact
    assert content.count("](data:image/png;base64,") == 2


def test_oversized_image_falls_back_to_explicit_text(monkeypatch, tmp_path):
    big = _make_png(tmp_path / "big.png", padded_size=MAX_EMBED_BYTES_PER_IMAGE + 1024)
    response = _run(
        monkeypatch,
        "assistant-core-image",
        {
            "output": "image faite",
            "artifact_type": "image",
            "artifact_paths": [str(big)],
        },
    )
    content = response["choices"][0]["message"]["content"]
    assert isinstance(content, str)
    assert "image faite" in content
    assert "taille supérieure à la limite d'intégration" in content
    assert str(big) not in content  # pas de leak du path brut


def test_missing_file_falls_back_to_explicit_text(monkeypatch, tmp_path):
    absent = tmp_path / "absent.png"
    response = _run(
        monkeypatch,
        "assistant-core-image",
        {
            "output": "image faite",
            "artifact_type": "image",
            "artifact_paths": [str(absent)],
        },
    )
    content = response["choices"][0]["message"]["content"]
    assert isinstance(content, str)
    assert "image faite" in content
    assert "fichier introuvable" in content
    assert str(absent) not in content


def test_max_embed_images_is_respected(monkeypatch, tmp_path):
    paths = [
        str(_make_png(tmp_path / f"v{i}.png"))
        for i in range(MAX_EMBED_IMAGES + 2)
    ]
    response = _run(
        monkeypatch,
        "assistant-core-image",
        {
            "output": "beaucoup de variantes",
            "artifact_type": "image",
            "artifact_paths": paths,
        },
    )
    content = response["choices"][0]["message"]["content"]
    assert isinstance(content, str)
    assert content.count("](data:image/png;base64,") == MAX_EMBED_IMAGES
