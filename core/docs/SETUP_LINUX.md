# Native Linux setup (Fedora)

> Native Linux is the **production / dev runtime** for AAC (fastest, `bwrap` isolation,
> EEVEE GPU rendering). If you only want to try the project, prefer the one-command
> Docker path in [`DOCKER.md`](DOCKER.md). This guide is for a full native install.
>
> Reference platform: **Fedora KDE** + NVIDIA GPU. Steps are described, not run for you;
> anything touching disk / boot / Secure Boot stays manual.

## What gets installed
- `scripts/setup-fedora.sh` — installs the dev toolchain on Fedora (idempotent).
- `core/env.linux.example` — env template for the single-host topology.
- `core/docker-compose.linux.yml` — service stack with SELinux `:z` labels.

---

## Installation

### 1. Dev toolchain
```bash
cd scripts
chmod +x setup-fedora.sh
./setup-fedora.sh            # interactive; --dry-run to preview
```
The script installs: RPM Fusion, git/zsh/modern CLI tools, Docker CE,
`nvidia-container-toolkit`, and creates the Python venv. It does **not** install the
NVIDIA driver by default (see the dedicated section below).

### 2. Project environment
```bash
cp core/env.linux.example core/.env   # then adjust values (secrets, ports)
```
> Never commit the real `.env`.

### 3. Container stack
```bash
# configure the GPU runtime once:
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker

docker compose -f core/docker-compose.linux.yml up
```

---

## API authentication

The API authenticates with a **bearer token**: requests carry an
`Authorization: Bearer <token>` header — exactly what OpenAI-compatible clients
(e.g. Open-WebUI) already send. The token is never logged; comparison is
constant-time.

### Environment variables (in `core/.env`)

| Variable | Role |
|---|---|
| `AAC_API_TOKEN` | The shared token. ≥ 16 chars. Generate with e.g. `python -c "import secrets;print(secrets.token_hex(24))"`. |
| `AAC_API_AUTH_MODE` | `off` \| `required`. Unset ⇒ `presence` (default). |
| `AAC_CONSOLE_ENABLED` | Mounts the `/console/*` console (browser UI, **unauthenticated**). On by default. |

### Postures

- **`presence`** (default, unset) — auth is enforced **if** `AAC_API_TOKEN` is set;
  otherwise the API is open with a startup warning. Convenient for loopback dev.
- **`off`** — auth never enforced (even with a token set).
- **`required`** — auth enforced **and** the app **refuses to start** if `AAC_API_TOKEN`
  is missing or invalid (fail-closed).

### Routes

- **Open**: `GET /health` (liveness probe).
- **Protected** (when auth is enforced): `/route`, `/execute`, `/debug/canonical`,
  `/health/runtime`, and any `/v1/*` → `401` + `WWW-Authenticate: Bearer` without a
  valid token.
- **Console** `/console/*`: not token-protected (browser UI). Mounted only if
  `AAC_CONSOLE_ENABLED` is on.

### Local vs exposed profile

- **Local profile** (default): bind `127.0.0.1`, console on, docs on.
  `AAC_API_TOKEN` optional.
- **Exposed / public profile** (before any non-loopback bind):
  ```bash
  AAC_API_AUTH_MODE=required
  AAC_API_TOKEN=<strong token>
  AAC_CONSOLE_ENABLED=0
  ```
  In `required` mode, `/docs`, `/redoc` and `/openapi.json` are automatically disabled.

> **Rule**: never bind the API beyond `127.0.0.1` without `AAC_API_AUTH_MODE=required`
> and a strong token. The console is not authenticated — do not expose it: keep it on
> loopback or disable it (`AAC_CONSOLE_ENABLED=0`) and expose only the API (e.g. via a
> reverse proxy that publishes only `/execute` and `/v1/*`).

---

## NVIDIA driver / Secure Boot

> ⚠️ Sensitive step: nothing irreversible is forced. Adapt to the actual state of your machine.

1. Install the driver via RPM Fusion (reversible):
   `./scripts/setup-fedora.sh --with-nvidia-driver`
2. Check Secure Boot: `mokutil --sb-state`
   - **enabled** → **MOK** enrollment at reboot (blue MOK Manager screen, password to enter).
   - **disabled** → no MOK.
3. Post-install checks, in order:
   ```bash
   mokutil --sb-state
   nvidia-smi                 # the GPU must appear
   ```
   then a real GPU test: Ollama inference + a Blender render (OptiX).

Until `nvidia-smi` responds, **do not move on**.

---

## End-to-end check
- `nvtop` shows the GPU active.
- `docker compose -f core/docker-compose.linux.yml up` → Ollama responds; backend `/health` OK.
- A Blender render produces `scene.blend` + `preview.png`.
