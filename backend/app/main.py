from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError

from .api.v1.health import router as health_router
from .api.v1.routes import router as core_router
from .api.v1.jarvis_routes import router as jarvis_router
from .database import apply_migrations
from .security.assistant_context import AccessDenied, InvalidTransition, NotFound
from .security.request_guard import local_header_error, has_ambiguous_singleton_headers
from .security.body_limit import RequestBodyLimitMiddleware
from .settings import API_PREFIX, APP_NAME

@asynccontextmanager
async def lifespan(_: FastAPI):
    apply_migrations()
    yield

app = FastAPI(title=APP_NAME, version="0.2.0", lifespan=lifespan)

@app.middleware("http")
async def local_request_guard(request: Request, call_next):
    if has_ambiguous_singleton_headers(request.scope.get("headers", [])):
        error = (400, "Ambiguous local request headers")
    else:
        error = local_header_error(request.headers.getlist("host"), request.headers.getlist("origin"), request.headers.get("sec-fetch-site", ""))
    if error is not None:
        response = JSONResponse(status_code=error[0], content={"detail": error[1]})
    else:
        response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response

@app.exception_handler(RequestValidationError)
async def validation_error_handler(_: Request, exc: RequestValidationError):
    # Never echo submitted values or invalid Unicode into an error response.
    details = []
    for error in exc.errors()[:20]:
        location = []
        for part in error.get("loc", ())[:10]:
            if isinstance(part, int):
                location.append(part)
            elif isinstance(part, str):
                location.append(part[:100].encode("utf-8", errors="replace").decode("utf-8"))
        details.append({"loc": location, "type": str(error.get("type", "invalid"))[:100]})
    return JSONResponse(status_code=422, content={"detail": details})

@app.exception_handler(NotFound)
async def not_found_handler(_: Request, exc: NotFound):
    return JSONResponse(status_code=404, content={"detail":str(exc)})

@app.exception_handler(AccessDenied)
async def access_denied_handler(_: Request, exc: AccessDenied):
    return JSONResponse(status_code=403, content={"detail":str(exc)})

@app.exception_handler(InvalidTransition)
async def invalid_transition_handler(_: Request, exc: InvalidTransition):
    return JSONResponse(status_code=409, content={"detail":str(exc)})

app.add_middleware(RequestBodyLimitMiddleware)

app.include_router(health_router, prefix=API_PREFIX)
app.include_router(core_router, prefix=API_PREFIX)
app.include_router(jarvis_router, prefix=API_PREFIX)
