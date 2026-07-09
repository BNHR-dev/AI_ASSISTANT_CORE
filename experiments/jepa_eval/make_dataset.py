"""Host orchestrator: push the dataset builder into the running AAC stack and run it.

The backend container has a read-only rootfs; only /outputs is writable, so the
scripts are streamed into /outputs/blender/_jepa_eval/_scripts/ through `docker exec`
(the host user cannot write to docker/outputs/ directly — container-owned files).

Usage: python make_dataset.py        (stack must be up: ./run.sh)
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

CONTAINER = "aac-aac-backend-1"
SCRIPTS_DIR = "/outputs/blender/_jepa_eval/_scripts"
HERE = Path(__file__).resolve().parent
PUSHED = ("incontainer_build_dataset.py", "mutate_and_render_template.py")


def push(name: str) -> None:
    content = (HERE / name).read_bytes()
    subprocess.run(
        ["docker", "exec", "-i", CONTAINER, "sh", "-c",
         f"mkdir -p {SCRIPTS_DIR} && cat > {SCRIPTS_DIR}/{name}"],
        input=content,
        check=True,
    )


def main() -> None:
    for name in PUSHED:
        push(name)
    print(f"scripts pushed to {CONTAINER}:{SCRIPTS_DIR}", flush=True)
    proc = subprocess.run(
        ["docker", "exec", CONTAINER, "python", f"{SCRIPTS_DIR}/incontainer_build_dataset.py"]
    )
    sys.exit(proc.returncode)


if __name__ == "__main__":
    main()
