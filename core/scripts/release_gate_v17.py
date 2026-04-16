from __future__ import annotations

import subprocess
import sys

SMOKE_TESTS = [
    "tests/test_comfyui_contract.py",
    "tests/test_comfyui_runtime.py",
    "tests/test_result_assembler_visibility.py",
    "tests/test_openai_compat_modes.py",
    "tests/test_runtime_debug_surface.py",
    "tests/test_output_contracts_v2a1.py",
    "tests/test_prompt_contracts_v2a1.py",
    "tests/test_executor_prompt_contracts_v2a1.py",
]

CORE_TESTS = [
    "tests/test_planner_service.py",
    "tests/test_multistep_executor.py",
    "tests/test_comfyui_contract.py",
    "tests/test_result_assembler_visibility.py",
    "tests/test_baseline_v17_regression.py",
    "tests/test_openai_compat_modes.py",
    "tests/test_comfyui_runtime.py",
    "tests/test_prompt_quality_v21.py",
    "tests/test_execution_summary.py",
    "tests/test_executor_visual_phase2.py",
    "tests/test_visual_artifact_contract.py",
    "tests/test_legacy_root_shims.py",
    "tests/test_runtime_debug_surface.py",
    "tests/test_output_contracts_v2a1.py",
    "tests/test_prompt_contracts_v2a1.py",
    "tests/test_executor_prompt_contracts_v2a1.py",
]


def run_bundle(name: str, tests: list[str]) -> int:
    print(f"=== RELEASE GATE {name} ===")
    cmd = [sys.executable, "-m", "pytest", *tests, "-q"]
    print(" ".join(cmd))
    completed = subprocess.run(cmd)
    print(f"=== RESULT {name}: {completed.returncode} ===")
    return completed.returncode


def main(argv: list[str]) -> int:
    mode = argv[1] if len(argv) > 1 else "core"

    if mode == "smoke":
        return run_bundle("SMOKE", SMOKE_TESTS)

    if mode == "core":
        return run_bundle("CORE", CORE_TESTS)

    print("Usage: python release_gate_v17.py [smoke|core]")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
