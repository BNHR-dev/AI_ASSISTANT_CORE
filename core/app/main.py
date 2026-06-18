from dotenv import load_dotenv
load_dotenv()
from fastapi import FastAPI

from openai_compat import router as openai_compat_router
from console import router as console_router

from app.engine.executor import execute_request
from app.engine.router_service import build_route_decision
from app.engine.runtime_debug import (
    get_canonical_boundaries,
    get_runtime_health,
)
from app.schemas import (
    CanonicalBoundariesResponse,
    ExecuteRequest,
    ExecuteResponse,
    RouteRequest,
    RouteResponse,
    RuntimeHealthResponse,
)


app = FastAPI(
    title="AI_ASSISTANT_CORE Router",
    version="1.7.0",
)

app.include_router(openai_compat_router)
app.include_router(console_router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/health/runtime", response_model=RuntimeHealthResponse)
def health_runtime() -> RuntimeHealthResponse:
    return RuntimeHealthResponse(**get_runtime_health())


@app.get("/debug/canonical", response_model=CanonicalBoundariesResponse)
def debug_canonical() -> CanonicalBoundariesResponse:
    return CanonicalBoundariesResponse(**get_canonical_boundaries())


@app.post("/route", response_model=RouteResponse)
def route_request(payload: RouteRequest) -> RouteResponse:
    decision = build_route_decision(
        payload.message,
        payload.has_image,
    )
    return RouteResponse(**decision)


@app.post("/execute", response_model=ExecuteResponse)
def execute(payload: ExecuteRequest) -> ExecuteResponse:
    result = execute_request(
        payload.message,
        payload.has_image,
    )
    return ExecuteResponse(**result)