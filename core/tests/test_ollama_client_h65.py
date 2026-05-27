"""
H.6.5.a — Tests du wrapper `generate_with_ollama` étendu.

Vérifie :
- rétrocompatibilité (pas de options/format → payload identique à avant) ;
- propagation correcte de options et format dans le payload ;
- gestion des erreurs HTTP ;
- timeout 240s inchangé.

Aucun appel Ollama réel — `requests.post` est mocké.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.clients import ollama_client


class _FakeResp:
    def __init__(self, ok: bool = True, status_code: int = 200,
                 json_payload: dict | None = None, text: str = ""):
        self.ok = ok
        self.status_code = status_code
        self._json = json_payload or {"response": "hello"}
        self.text = text

    def json(self):
        return self._json


def test_default_payload_backward_compatible():
    fake = _FakeResp()
    with patch("app.clients.ollama_client.requests.post",
               return_value=fake) as mocked:
        out = ollama_client.generate_with_ollama("m", "p")
    assert out == "hello"
    call = mocked.call_args
    payload = call.kwargs["json"]
    # Strictement le payload pré-H.6.5.a.
    assert payload == {"model": "m", "prompt": "p", "stream": False}
    assert call.kwargs["timeout"] == 240


def test_options_propagated_to_payload():
    fake = _FakeResp()
    options = {"temperature": 0.0, "seed": 42, "top_p": 1.0, "num_ctx": 4096}
    with patch("app.clients.ollama_client.requests.post",
               return_value=fake) as mocked:
        ollama_client.generate_with_ollama("m", "p", options=options)
    payload = mocked.call_args.kwargs["json"]
    assert payload["options"] == options
    # Les autres clés restent inchangées.
    assert payload["model"] == "m"
    assert payload["prompt"] == "p"
    assert payload["stream"] is False
    assert "format" not in payload


def test_format_propagated_to_payload():
    fake = _FakeResp()
    with patch("app.clients.ollama_client.requests.post",
               return_value=fake) as mocked:
        ollama_client.generate_with_ollama("m", "p", format="json")
    payload = mocked.call_args.kwargs["json"]
    assert payload["format"] == "json"
    assert "options" not in payload


def test_options_and_format_together():
    fake = _FakeResp()
    with patch("app.clients.ollama_client.requests.post",
               return_value=fake) as mocked:
        ollama_client.generate_with_ollama(
            "m", "p",
            options={"temperature": 0.0},
            format="json",
        )
    payload = mocked.call_args.kwargs["json"]
    assert payload["options"] == {"temperature": 0.0}
    assert payload["format"] == "json"


def test_options_dict_is_copied_not_aliased():
    """Le payload contient une copie ; mutation externe ne corrompt pas le call."""
    fake = _FakeResp()
    src = {"temperature": 0.0}
    with patch("app.clients.ollama_client.requests.post",
               return_value=fake) as mocked:
        ollama_client.generate_with_ollama("m", "p", options=src)
    src["temperature"] = 999.0  # mutate after call
    payload = mocked.call_args.kwargs["json"]
    assert payload["options"]["temperature"] == 0.0


def test_http_error_raises_runtime_error():
    fake = _FakeResp(ok=False, status_code=500, text="boom")
    with patch("app.clients.ollama_client.requests.post", return_value=fake):
        with pytest.raises(RuntimeError, match="Ollama error 500"):
            ollama_client.generate_with_ollama("m", "p", format="json")


def test_response_field_stripped():
    fake = _FakeResp(json_payload={"response": "  hi  \n"})
    with patch("app.clients.ollama_client.requests.post", return_value=fake):
        out = ollama_client.generate_with_ollama("m", "p")
    assert out == "hi"
