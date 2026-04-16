import requests

from app.infra.runtime_urls import get_ollama_generate_url


def generate_with_ollama(model: str, prompt: str) -> str:
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
    }

    response = requests.post(get_ollama_generate_url(), json=payload, timeout=240)

    if not response.ok:
        raise RuntimeError(
            f"Ollama error {response.status_code}: {response.text}"
        )

    data = response.json()
    return data.get("response", "").strip()
