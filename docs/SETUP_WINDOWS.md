# Windows setup

Two ways to run AAC on Windows. **Pick the first one unless you have a reason not to.**

## 1. Docker Desktop + WSL2 — recommended (secure)

This is the **canonical secure path**: the backend runs inside the same hardened Linux
container as on Linux (WSL2 backend), so you get the same confinement **by construction** —
`cap_drop: ALL`, `no-new-privileges`, read-only rootfs, no extra privileges; the launcher
always mounts the same hardened overlay. Nothing is reimplemented for Windows.

> **Honest status:** this path is documented but **not yet validated end to end on
> Windows** (see [`DOCKER.md`](DOCKER.md), *Implementation status*). Bring-your-own-Ollama
> ([`OLLAMA.md`](OLLAMA.md)) is wired into `run.sh` only — the Windows launcher does not
> implement it yet.

**Prerequisites**
- [Docker Desktop](https://www.docker.com/products/docker-desktop/) with the **WSL2 backend**
  enabled.
- For GPU image/3D generation: an NVIDIA driver + Docker Desktop's WSL2 GPU support
  (CUDA on WSL2). No GPU → it still runs on CPU.

**Run**
```bat
run.bat            REM double-click, or from cmd.exe / PowerShell — builds, pulls models,
                   REM checks real health, opens the Console
run.bat --down     REM stop the stack
run.bat --logs     REM follow logs
```
`run.bat` delegates to `run.ps1`. First run builds images and downloads ~6.6 GB of image
models + the LLM models — expect a long first start. Backend on `http://127.0.0.1:8000`.

> Model download shells out to bash scripts → a WSL or Git-bash `bash` must be on `PATH`
> (Docker Desktop + WSL2 provides one).

## 2. Native install — advanced, **NOT sandboxed**

`scripts\windows\Install-AAC.bat` installs the missing pieces **natively, without Docker**:
Ollama (direct download from ollama.com) + the 3 generation models, ComfyUI portable
(GitHub release — needs 7-Zip already present), the backend venv and the launch scripts.
Python 3.11–3.13 and Blender are **prerequisites it detects but does not install**;
SearXNG still needs Docker Desktop.

> ⚠️ **No OS-level sandbox.** bubblewrap is Linux-only, so on native Windows the generated
> Blender `bpy` code runs **without OS confinement**. Use this path only if you understand
> that trade-off; for the confined/secure path use option 1 (Docker Desktop + WSL2).

```bat
scripts\windows\Install-AAC.bat              REM full install
scripts\windows\Install-AAC.bat -CheckOnly   REM doctor — checks only, installs nothing
scripts\windows\Install-AAC.bat -SkipComfyUI REM skip the heaviest phase
```
When done, the script prints the backend start command (`Start-AAC.bat` /
`scripts\windows\Start-AAC.ps1`). Full dependency inventory → [`DEPENDENCIES.md`](DEPENDENCIES.md).
