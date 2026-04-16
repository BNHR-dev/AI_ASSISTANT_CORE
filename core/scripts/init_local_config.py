from __future__ import annotations

import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_EXAMPLE = ROOT / ".env.example"
ENV_FILE = ROOT / ".env"
SEARXNG_EXAMPLE = ROOT / "searxng" / "settings.example.yml"
SEARXNG_FILE = ROOT / "searxng" / "settings.yml"
PLACEHOLDERS = {"change-me", '"change-me"'}


def generate_secret_hex(length: int = 32) -> str:
    return secrets.token_hex(length)


def ensure_env_file() -> tuple[bool, bool]:
    created = False
    changed = False

    if not ENV_FILE.exists():
        text = ENV_EXAMPLE.read_text(encoding="utf-8")
        value = generate_secret_hex()
        text = text.replace("WEBUI_SECRET_KEY=change-me", f"WEBUI_SECRET_KEY={value}")
        ENV_FILE.write_text(text, encoding="utf-8")
        return True, True

    lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
    new_lines: list[str] = []
    found = False

    for line in lines:
        if line.startswith("WEBUI_SECRET_KEY="):
            found = True
            current = line.split("=", 1)[1].strip()
            if current in PLACEHOLDERS or not current:
                line = f"WEBUI_SECRET_KEY={generate_secret_hex()}"
                changed = True
        new_lines.append(line)

    if not found:
        new_lines.append(f"WEBUI_SECRET_KEY={generate_secret_hex()}")
        changed = True

    if changed:
        ENV_FILE.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

    return created, changed


def ensure_searxng_settings() -> tuple[bool, bool]:
    created = False
    changed = False

    if not SEARXNG_FILE.exists():
        text = SEARXNG_EXAMPLE.read_text(encoding="utf-8")
        value = generate_secret_hex()
        text = text.replace('secret_key: "change-me"', f'secret_key: "{value}"')
        SEARXNG_FILE.write_text(text, encoding="utf-8")
        return True, True

    lines = SEARXNG_FILE.read_text(encoding="utf-8").splitlines()
    new_lines: list[str] = []
    found = False

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("secret_key:"):
            found = True
            current = stripped.split(":", 1)[1].strip()
            if current in PLACEHOLDERS or current.strip('"') == "change-me":
                indent = line[: len(line) - len(line.lstrip())]
                line = f'{indent}secret_key: "{generate_secret_hex()}"'
                changed = True
        new_lines.append(line)

    if not found:
        new_lines.append(f'secret_key: "{generate_secret_hex()}"')
        changed = True

    if changed:
        SEARXNG_FILE.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

    return created, changed


def main() -> int:
    env_created, env_changed = ensure_env_file()
    searxng_created, searxng_changed = ensure_searxng_settings()

    print("=== LOCAL CONFIG BOOTSTRAP ===")
    print(f".env created: {env_created} | secret updated: {env_changed}")
    print(f"searxng/settings.yml created: {searxng_created} | secret updated: {searxng_changed}")
    print("Local secrets stay outside Git.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())