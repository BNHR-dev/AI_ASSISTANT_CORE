# AI_ASSISTANT_CORE — project rules for Claude Code

## Project identity

AI_ASSISTANT_CORE is a local AI orchestrator oriented toward digital creation and
structured execution.

The product core is:
- a router
- a planner
- an executor
- an observability surface

The system takes a request, produces a structured decision, builds an execution plan,
runs that plan step by step, then returns a useful, traceable output.

## Security — absolute rule (private keys)

- **NEVER read a private SSH key (or any private key) on this machine.** Private key
  files under `~/.ssh/` (`id_ed25519`, `id_rsa`, etc. — everything except `*.pub`) are
  strictly off limits. Public keys (`*.pub`), `config`, and `known_hosts` remain readable.
- This is a **non-negotiable** invariant, **mechanically enforced** by a global
  `PreToolUse` hook (`~/.claude/hooks/block-ssh-private-keys.sh`, declared in
  `~/.claude/settings.json`) that blocks any `Read`/`Bash`/`Grep` call targeting a private key.
- Rationale: a private key proves the author's identity and authority; its confidentiality
  must never depend on a third-party tool.

## Invariants

- Do not propose a global architecture overhaul or a from-scratch rewrite
- Do not break the `router + planner + executor` core
- Work from the real, validated state
- Favor short, concrete, high-value, reversible phases
- Preserve readability, robustness, and determinism
- Avoid premature complexity

## Architecture

Single repo, two runtimes. **Recommended secure path = the hardened Docker stack** (`docker/`,
via `./run.sh` / `run.bat`): rootful Docker, `cap_drop: ALL`, **no `SYS_ADMIN`**,
`AAC_BLENDER_SANDBOX=off` — the container is the confinement boundary (not bubblewrap).

- **Native Linux/Fedora** stays available: the Fedora KDE host as workspace + runtime, host
  `bubblewrap` confining the generated code, Blender headless on the host GPU (RTX 3060),
  services via `docker/docker-compose.linux.yml`, ports bound to 127.0.0.1.
- the old Windows-host + Ubuntu-VM context is gone from the tree (git history only) — do
  not treat it as the current architecture
- generated code runs OS-confined via bubblewrap today (`AAC_BLENDER_SANDBOX`); a stronger
  VM-grade isolation remains a product goal (roadmap)

## Source of truth

When information diverges, the order of trust is:

1. `core/app/*`
2. `core/openai_compat.py`
3. the project's canonical documentation
4. the runtime config actually in use

Do not treat the legacy root files as a business source when `app/*` says otherwise.

## Blender pipeline

The Blender pipeline is experimental but functional. It does not change the invariants of
the router + planner + executor core.

- canonical client: `core/app/clients/blender_client.py`
- validated outputs: `scene.py`, `scene.blend`, `preview.png` under `outputs/blender/<uuid>/`
- `scene.blend` is the canonical artifact
- `preview.png` is best-effort and non-blocking
- the preview render runs in a separate subprocess, distinct from the main script

## Working rules

- Clearly distinguish code, docs, declared runtime, declared security, and legacy (Windows/VM)
- Do not claim to have checked the live state of the firewall, systemd, or services unless
  it is visible in the repo or via an executed command
- Surface ambiguities explicitly
- Prefer short, reversible patches
- For multi-file or ambiguous tasks, start with a plan
