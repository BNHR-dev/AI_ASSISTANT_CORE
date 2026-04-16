from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_health_runtime_endpoint_exposes_runtime_status(monkeypatch):
    monkeypatch.setattr(
        "app.main.get_runtime_health",
        lambda: {
            "status": "partial",
            "version": "1.7.0",
            "checked_at": "2026-04-06T10:00:00+00:00",
            "summary": "core runtime ready; optional services unavailable: comfyui",
            "services": {
                "ollama": {
                    "name": "ollama",
                    "ready": True,
                    "required": True,
                    "role": "llm_backend",
                    "reason": "http 200",
                    "endpoint": "http://localhost:12000/api/tags",
                    "activity": "active",
                },
                "searxng": {
                    "name": "searxng",
                    "ready": True,
                    "required": False,
                    "role": "web_search",
                    "reason": "http 200",
                    "endpoint": "http://localhost:8081/search?q=test&format=json",
                    "activity": "optional",
                },
                "comfyui": {
                    "name": "comfyui",
                    "ready": False,
                    "required": False,
                    "role": "visual_generation",
                    "reason": "connection refused",
                    "endpoint": "http://127.0.0.1:8188",
                    "activity": "optional",
                },
            },
        },
    )

    response = client.get("/health/runtime")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "partial"
    assert body["services"]["ollama"]["ready"] is True
    assert body["services"]["comfyui"]["ready"] is False
    assert body["services"]["ollama"]["endpoint"] == "http://localhost:12000/api/tags"



def test_debug_canonical_endpoint_exposes_project_boundaries(monkeypatch):
    monkeypatch.setattr(
        "app.main.get_canonical_boundaries",
        lambda: {
            "status": "ok",
            "version": "1.7.0",
            "canonical_paths": ["app/*", "openai_compat.py"],
            "legacy_shims": ["executor.py"],
            "active_runtime_modules": ["app/engine/executor.py"],
            "active_auxiliary_modules": ["app/infra/tool_manager.py"],
            "optional_runtime_services": ["comfyui"],
            "dormant_modules": ["app/engine/planner.py"],
            "rule": "app/* defines runtime behavior.",
        },
    )

    response = client.get("/debug/canonical")

    assert response.status_code == 200
    body = response.json()
    assert body["canonical_paths"] == ["app/*", "openai_compat.py"]
    assert body["legacy_shims"] == ["executor.py"]
    assert body["active_runtime_modules"] == ["app/engine/executor.py"]
    assert body["active_auxiliary_modules"] == ["app/infra/tool_manager.py"]
    assert body["optional_runtime_services"] == ["comfyui"]
    assert body["dormant_modules"] == ["app/engine/planner.py"]
