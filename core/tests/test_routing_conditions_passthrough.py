import pytest

from app.engine.routing_conditions import enrich_route_config
from app.engine.task_routing import TASK_ROUTING

# Task types that must never receive a second_call from enrich_route_config.
# Any task_type not explicitly handled in enrich_route_config must pass through unchanged.
_HYBRID_TASK_TYPES = {"explain_basic", "critique", "architecture"}
_PASSTHROUGH_TASK_TYPES = sorted(set(TASK_ROUTING) - _HYBRID_TASK_TYPES)

# Most triggering text: hits code_request + improvement_request + implementation_request
_TRIGGER_TEXT = (
    "améliore ce script python et donne un exemple de code "
    "avec une implémentation simple, optimize et refactor"
)


@pytest.mark.parametrize("task_type", _PASSTHROUGH_TASK_TYPES)
def test_passthrough_task_type_never_gets_second_call(task_type):
    route = TASK_ROUTING[task_type]
    base = {
        "task_type": task_type,
        "primary_agent": route.primary_agent,
        "selected_model": route.model,
        "needs_web": route.web,
        "output_format": route.output_format,
    }

    result = enrich_route_config(task_type, _TRIGGER_TEXT, base)

    assert result["second_call"] is None, (
        f"'{task_type}' must not receive a second_call from enrich_route_config, "
        f"got: {result['second_call']!r}"
    )
    assert result["matched_rule"] is None, (
        f"'{task_type}' must not match any hybrid rule, got: {result['matched_rule']!r}"
    )


def test_passthrough_task_types_declare_null_second_call_in_routing():
    for task_type in _PASSTHROUGH_TASK_TYPES:
        route = TASK_ROUTING[task_type]
        assert route.second_call is None, (
            f"TASK_ROUTING['{task_type}'].second_call must be None, got: {route.second_call!r}"
        )


def test_passthrough_set_covers_all_non_hybrid_routes():
    all_types = set(TASK_ROUTING)
    covered = _PASSTHROUGH_TASK_TYPES + list(_HYBRID_TASK_TYPES)
    assert all_types == set(covered), (
        f"Uncovered task_types: {all_types - set(covered)}"
    )
