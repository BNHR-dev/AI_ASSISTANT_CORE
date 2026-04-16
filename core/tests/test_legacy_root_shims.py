import importlib


def test_executor_root_shim_points_to_canonical_module():
    legacy = importlib.import_module("executor")
    canonical = importlib.import_module("app.engine.executor")
    assert legacy.execute_request is canonical.execute_request


def test_router_service_root_shim_points_to_canonical_module():
    legacy = importlib.import_module("router_service")
    canonical = importlib.import_module("app.engine.router_service")
    assert legacy.build_route_decision is canonical.build_route_decision


def test_task_classifier_root_shim_points_to_canonical_module():
    legacy = importlib.import_module("task_classifier")
    canonical = importlib.import_module("app.task_classifier")
    assert legacy.classify_task is canonical.classify_task


def test_tool_selector_root_shim_points_to_canonical_module():
    legacy = importlib.import_module("tool_selector")
    canonical = importlib.import_module("app.tool_selector")
    assert legacy.select_tool is canonical.select_tool


def test_task_routing_root_shim_points_to_canonical_module():
    legacy = importlib.import_module("task_routing")
    canonical = importlib.import_module("app.engine.task_routing")
    assert legacy.TASK_ROUTING is canonical.TASK_ROUTING


def test_comfyui_client_root_shim_points_to_canonical_module():
    legacy = importlib.import_module("comfyui_client")
    canonical = importlib.import_module("app.clients.comfyui_client")
    assert legacy.run_comfyui_workflow is canonical.run_comfyui_workflow
