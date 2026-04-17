import json
import os
import sys

def deny(reason: str) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason
        }
    }))
    sys.exit(0)

def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    file_path = (
        payload.get("tool_input", {}).get("file_path")
        or payload.get("tool_input", {}).get("path")
        or ""
    )

    if not file_path:
        sys.exit(0)

    normalized = file_path.replace("\\", "/").lower()

    protected_patterns = [
        "/.env",
        ".env.",
        "/.git/",
        "searxng/settings.yml",
        "/id_ed25519",
        ".pem",
        ".key",
        "secret"
    ]

    for pattern in protected_patterns:
        if pattern in normalized:
            deny(f"Blocked edit: protected file pattern matched: {pattern}")

    sys.exit(0)

if __name__ == "__main__":
    main()