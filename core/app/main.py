from dotenv import load_dotenv
load_dotenv()

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException

from openai_compat import router as openai_compat_router

from app.auth import (
    console_enabled,
    docs_enabled,
    log_auth_posture,
    require_api_token,
    validate_startup_auth,
)
from app.engine.executor import execute_request, resume_request
from app.engine.reproduce import reproduce_run
from app.engine.router_service import build_route_decision
from app.engine.runtime_debug import (
    get_canonical_boundaries,
    get_runtime_health,
)
from app.schemas import (
    CanonicalBoundariesResponse,
    ExecuteRequest,
    ExecuteResponse,
    ReproduceRequest,
    ReproduceResponse,
    ResumeRequest,
    RouteRequest,
    RouteResponse,
    RuntimeHealthResponse,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # C2 — fail-closed : en mode required sans token valide, refus de démarrer.
    validate_startup_auth()
    log_auth_posture()
    yield


# Handlers définis au niveau module : importables et patchables par les tests
# (`from app.main import execute`, `monkeypatch app.main.execute_request`).
# L'authentification n'est PAS dans le corps du handler — elle est attachée à
# l'enregistrement de la route dans create_app (Depends), donc un appel direct
# de la fonction en test reste possible.

def health() -> dict:
    return {"status": "ok"}


def health_runtime() -> RuntimeHealthResponse:
    return RuntimeHealthResponse(**get_runtime_health())


def debug_canonical() -> CanonicalBoundariesResponse:
    return CanonicalBoundariesResponse(**get_canonical_boundaries())


def route_request(payload: RouteRequest) -> RouteResponse:
    decision = build_route_decision(
        payload.message,
        payload.has_image,
    )
    return RouteResponse(**decision)


def execute(payload: ExecuteRequest) -> ExecuteResponse:
    result = execute_request(
        payload.message,
        payload.has_image,
    )
    return ExecuteResponse(**result)


def resume(payload: ResumeRequest) -> ExecuteResponse:
    try:
        result = resume_request(payload.request_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return ExecuteResponse(**result)


def reproduce(payload: ReproduceRequest) -> ReproduceResponse:
    # Clés JSON = str ; l'engine indexe les variantes en int (index du manifest).
    workflows = {
        int(index): workflow
        for index, workflow in payload.workflows.items()
        if index.isdigit()
    }
    report = reproduce_run(
        payload.pipeline,
        payload.manifest,
        workflows=workflows,
        scene_py=payload.scene_py,
    )
    return ReproduceResponse(**report)


def create_app() -> FastAPI:
    """Construit l'app en lisant la posture d'auth dans l'environnement.

    Factory (et non app figée) pour que docs activés, console montée et auth
    soient testables par mode sans recharger le module.
    """
    # Profil exposé (required) : /docs, /redoc, /openapi.json désactivés.
    docs_kwargs: dict = {}
    if not docs_enabled():
        docs_kwargs = {"docs_url": None, "redoc_url": None, "openapi_url": None}

    app = FastAPI(
        title="AI_ASSISTANT_CORE Router",
        version="1.7.0",
        lifespan=lifespan,
        **docs_kwargs,
    )

    # C2 — dépendance d'auth appliquée aux surfaces sensibles. /health reste
    # ouvert (sonde de vie). La console (/console/*) n'est PAS protégée par token
    # (UI navigateur) : elle est seulement montée si explicitement activée.
    protected = [Depends(require_api_token)]

    app.include_router(openai_compat_router, dependencies=protected)

    if console_enabled():
        from console import router as console_router
        app.include_router(console_router)

    app.add_api_route("/health", health, methods=["GET"])
    app.add_api_route(
        "/health/runtime", health_runtime, methods=["GET"],
        response_model=RuntimeHealthResponse, dependencies=protected,
    )
    app.add_api_route(
        "/debug/canonical", debug_canonical, methods=["GET"],
        response_model=CanonicalBoundariesResponse, dependencies=protected,
    )
    app.add_api_route(
        "/route", route_request, methods=["POST"],
        response_model=RouteResponse, dependencies=protected,
    )
    app.add_api_route(
        "/execute", execute, methods=["POST"],
        response_model=ExecuteResponse, dependencies=protected,
    )
    app.add_api_route(
        "/resume", resume, methods=["POST"],
        response_model=ExecuteResponse, dependencies=protected,
    )
    app.add_api_route(
        "/reproduce", reproduce, methods=["POST"],
        response_model=ReproduceResponse, dependencies=protected,
    )

    return app


app = create_app()
