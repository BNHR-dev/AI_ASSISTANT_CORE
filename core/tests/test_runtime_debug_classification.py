"""
Structural lock on the canonical classification exposed by /debug/canonical.

Goal:
    Prevent silent drift between what actually lives in app/* and what
    get_canonical_boundaries() reports as runtime / auxiliary / dormant.

These tests do NOT mock anything. They read the real file system and the
real constants from app.engine.runtime_debug, and verify that the three
lists (ACTIVE_RUNTIME_MODULES, ACTIVE_AUXILIARY_MODULES, DORMANT_MODULES)
together form a disjoint, exhaustive cover of every importable app/*.py
(ignoring package markers).

If someone adds a new module under app/ without classifying it, these
tests fail with a clear message naming the unclassified file.
"""
from __future__ import annotations

from pathlib import Path

from app.engine.runtime_debug import (
    ACTIVE_AUXILIARY_MODULES,
    ACTIVE_RUNTIME_MODULES,
    DORMANT_MODULES,
    LEGACY_SHIMS,
    get_canonical_boundaries,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
APP_DIR = REPO_ROOT / "app"


# Minimal set of modules that MUST stay in the runtime list. If any of these
# ever falls out, it means the canonical boundary is lying about the decision
# flow. This is the anti-regression floor.
CRITICAL_RUNTIME_MODULES = frozenset(
    {
        "app/main.py",
        "app/task_classifier.py",
        "app/tool_selector.py",
        "app/engine/router_service.py",
        "app/engine/planner_service.py",
        "app/engine/plan_builder.py",
        "app/engine/executor.py",
        "app/engine/step_executor.py",
        "app/engine/result_assembler.py",
    }
)


def _rel(path: Path) -> str:
    """Return the path relative to repo root, with forward slashes."""
    return path.relative_to(REPO_ROOT).as_posix()


def _discover_app_modules() -> set[str]:
    """
    Return every .py file under app/ that is not a package marker.

    __init__.py files are excluded because they are Python packaging glue,
    not code that carries behavior in the classification sense.
    """
    discovered: set[str] = set()
    for py_file in APP_DIR.rglob("*.py"):
        if py_file.name == "__init__.py":
            continue
        discovered.add(_rel(py_file))
    return discovered


def test_every_listed_module_exists_on_disk():
    """Every entry in the three lists must point to a file that actually exists."""
    missing: list[str] = []
    for entry in ACTIVE_RUNTIME_MODULES + ACTIVE_AUXILIARY_MODULES + DORMANT_MODULES:
        if not (REPO_ROOT / entry).is_file():
            missing.append(entry)
    assert not missing, (
        "runtime_debug references files that do not exist: " + ", ".join(missing)
    )


def test_three_lists_are_pairwise_disjoint():
    """A module cannot be runtime AND auxiliary, or runtime AND dormant, etc."""
    runtime = set(ACTIVE_RUNTIME_MODULES)
    auxiliary = set(ACTIVE_AUXILIARY_MODULES)
    dormant = set(DORMANT_MODULES)

    assert not runtime & auxiliary, (
        "modules listed in both runtime and auxiliary: "
        + ", ".join(sorted(runtime & auxiliary))
    )
    assert not runtime & dormant, (
        "modules listed in both runtime and dormant: "
        + ", ".join(sorted(runtime & dormant))
    )
    assert not auxiliary & dormant, (
        "modules listed in both auxiliary and dormant: "
        + ", ".join(sorted(auxiliary & dormant))
    )


def test_every_app_module_is_classified():
    """
    Every non-__init__ app/*.py must be in exactly one of the three lists.

    If this test fails, either add the new module to the correct list in
    app/engine/runtime_debug.py, or justify why it should not appear at all.
    """
    classified = (
        set(ACTIVE_RUNTIME_MODULES)
        | set(ACTIVE_AUXILIARY_MODULES)
        | set(DORMANT_MODULES)
    )
    discovered = _discover_app_modules()

    unclassified = discovered - classified
    assert not unclassified, (
        "these app/ modules are not classified in runtime_debug.py "
        "(add them to runtime, auxiliary, or dormant): "
        + ", ".join(sorted(unclassified))
    )


def test_classified_modules_exist_in_app_tree():
    """
    Each classified app/* entry must correspond to a real file under app/.

    Catches stale entries left behind after a file rename or deletion.
    """
    classified_under_app = {
        entry
        for entry in ACTIVE_RUNTIME_MODULES + ACTIVE_AUXILIARY_MODULES + DORMANT_MODULES
        if entry.startswith("app/")
    }
    discovered = _discover_app_modules()

    stale = classified_under_app - discovered
    assert not stale, (
        "runtime_debug lists app/ modules that no longer exist on disk: "
        + ", ".join(sorted(stale))
    )


def test_critical_runtime_modules_stay_in_runtime():
    """Anti-regression floor: the decision flow backbone stays classified as runtime."""
    runtime = set(ACTIVE_RUNTIME_MODULES)
    missing = CRITICAL_RUNTIME_MODULES - runtime
    assert not missing, (
        "critical runtime modules missing from ACTIVE_RUNTIME_MODULES "
        "(decision flow must stay exposed as canonical): "
        + ", ".join(sorted(missing))
    )


def test_legacy_root_shims_are_not_listed_as_app_runtime():
    """
    The root-level shims (executor.py, router_service.py, etc.) are NOT canonical
    app/* modules. They must not leak into any of the three app/ classification
    lists via accidental string edit.
    """
    classification = (
        set(ACTIVE_RUNTIME_MODULES)
        | set(ACTIVE_AUXILIARY_MODULES)
        | set(DORMANT_MODULES)
    )
    root_shim_leakage = {entry for entry in LEGACY_SHIMS if entry in classification}
    assert not root_shim_leakage, (
        "legacy root shims must stay in LEGACY_SHIMS only, not in runtime/auxiliary/dormant: "
        + ", ".join(sorted(root_shim_leakage))
    )


def test_canonical_boundaries_payload_matches_module_constants():
    """The /debug/canonical payload must expose exactly the classification constants."""
    payload = get_canonical_boundaries()

    assert payload["active_runtime_modules"] == ACTIVE_RUNTIME_MODULES
    assert payload["active_auxiliary_modules"] == ACTIVE_AUXILIARY_MODULES
    assert payload["dormant_modules"] == DORMANT_MODULES
    assert payload["legacy_shims"] == LEGACY_SHIMS
