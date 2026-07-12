# Bring your own Ollama

AAC ships with a bundled Ollama container, but nothing ties it to that instance. Point the backend at any Ollama — native, LAN, remote, or another container — and swap the models it serves. No code change, environment variables only. Without any of these variables set, behavior is exactly the bundled default.

## Endpoint

| Variable | Meaning | Default |
|---|---|---|
| `OLLAMA_BASE_URL` | Root of the Ollama instance (e.g. `http://192.168.1.50:11434`) | bundled container |
| `AAC_OLLAMA_TIMEOUT` | Generation timeout in seconds | `240` |

`OLLAMA_GENERATE_URL` / `OLLAMA_TAGS_URL` remain available for split deployments where the two APIs live behind different proxies; most setups only need `OLLAMA_BASE_URL`.

## Models

Models are configured by **role**. Each role has the historical default, so an unset variable changes nothing.

| Variable | Role | Default |
|---|---|---|
| `AAC_OLLAMA_GENERAL_MODEL` | Explanations, critique, synthesis, routing default | `qwen3:8b` |
| `AAC_OLLAMA_CODER_MODEL` | Code generation (`build` tasks); also the Blender fallback | `qwen2.5-coder:7b` |
| `AAC_OLLAMA_VISION_MODEL` | Requests with an image | `qwen2.5vl:3b` |
| `AAC_BLENDER_LLM_MODEL` | Blender `bpy` script generation (wins over the coder role) | `qwen2.5-coder:7b` |
| `AAC_EMBED_MODEL` | Embeddings (semantic router fallback) | `bge-m3` |

The embedding model is **optional**: when it is absent from the instance, the semantic router layer degrades to the historical rule-only behavior and health flags it — nothing breaks.

## Examples

**Native Ollama on the same machine** (default port):

```bash
OLLAMA_BASE_URL=http://127.0.0.1:11434 ./run.sh
```

**A shared LAN box with different models:**

```bash
export OLLAMA_BASE_URL=http://192.168.1.50:11434
export AAC_OLLAMA_GENERAL_MODEL=llama3.3:70b
export AAC_OLLAMA_CODER_MODEL=qwen2.5-coder:32b
./run.sh
```

**Remote instance:** reach it through your own tunnel (e.g. `ssh -L 11434:localhost:11434 gpu-box`) and point `OLLAMA_BASE_URL` at the local end. AAC does not store credentials and does not add auth headers — keep the transport private.

**Docker Compose override** (backend container talking to an external instance):

```yaml
# docker-compose.override.yml
services:
  aac-backend:
    environment:
      OLLAMA_BASE_URL: "http://192.168.1.50:11434"
      AAC_OLLAMA_GENERAL_MODEL: "llama3.3:70b"
```

## Verify the instance

`GET /health/runtime` (or the Console health strip) tells you whether the instance can actually serve the current configuration, not just whether it answers HTTP:

- **unreachable** → the transport error and the endpoint that was probed;
- **reachable but not ready** → the exact list of missing generation models (`missing: [...]`) — pull them with `ollama pull <name>`;
- **ready** → every configured generation model is present. A missing embedding model is reported as optional and does not degrade readiness.

A required name without a tag matches any installed tag (`bge-m3` matches `bge-m3:latest`), mirroring `ollama pull` semantics.

Generation errors are actionable by construction: an unreachable instance names the endpoint and the variable to check; a 404 on a missing model suggests the `ollama pull` command.

## Provenance

Every v2 run manifest records the Ollama environment that produced the run — endpoint, server version, and the resolved model per role — under `repro.ollama`. The Console run detail shows it as a badge. Replays do not call Ollama back, so this block is informational and never drives a reproduce verdict.
