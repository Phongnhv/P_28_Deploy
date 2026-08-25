
import os
import sys
from dotenv import load_dotenv

load_dotenv()

# Only enable Phoenix OpenTelemetry tracing when explicitly requested and not in test/disabled mode
if "pytest" not in sys.modules and not os.getenv("DISABLE_TRACING") and (os.getenv("ENABLE_PHOENIX") == "true" or os.getenv("PHOENIX_COLLECTOR_ENDPOINT")):
    try:
        from openinference.instrumentation.langchain import LangChainInstrumentor
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        phoenix_host = "localhost" if os.name == "nt" or not os.path.exists("/.dockerenv") else "host.docker.internal"
        phoenix_endpoint = os.getenv("PHOENIX_COLLECTOR_ENDPOINT") or f"http://{phoenix_host}:6006/v1/traces"
        tracer_provider = TracerProvider()
        tracer_provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=phoenix_endpoint))
        )
        trace.set_tracer_provider(tracer_provider)
        LangChainInstrumentor().instrument()
    except Exception:
        pass



import logging
import os
import uuid
from contextlib import asynccontextmanager
from urllib.parse import urlsplit, urlunsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.api.data_access_routes import router as data_access_router
from src.api.routes import dq_router, router
from src.config import get_settings
from src.services.rule_store import get_engine, init_db

logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    init_db()
    print(f"Starting {settings.app_name} in {settings.app_env} mode")
    yield
    print("Shutting down...")

settings = get_settings()

app = FastAPI(
    title="AI20K Agent",
    description="RidePulse DQ Localhost MVP",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None if settings.app_env == "production" else "/docs",
    redoc_url=None if settings.app_env == "production" else "/redoc",
    openapi_url=None if settings.app_env == "production" else "/openapi.json",
)

# CORS Configuration
frontend_origin_env = os.getenv("FRONTEND_ORIGIN")
origins = settings.cors_origins.split(",")

if frontend_origin_env:
    origins.extend([o.strip() for o in frontend_origin_env.split(",") if o.strip()])

loopback_origins: set[str] = set()
for origin in origins:
    clean_origin = origin.strip()
    if not clean_origin:
        continue
    loopback_origins.add(clean_origin)
    parsed = urlsplit(clean_origin)
    if parsed.hostname in {"localhost", "127.0.0.1"}:
        twin_host = "127.0.0.1" if parsed.hostname == "localhost" else "localhost"
        twin_netloc = f"{twin_host}:{parsed.port}" if parsed.port else twin_host
        loopback_origins.add(urlunsplit((parsed.scheme, twin_netloc, parsed.path, "", "")))

allow_origins = sorted(loopback_origins)

app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Exception handlers for stable error envelope (Step 8)
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    request_id = str(uuid.uuid4())
    errors = exc.errors()
    messages = []
    for err in errors:
        loc = " -> ".join(str(x) for x in err["loc"])
        messages.append(f"{loc}: {err['msg']}")
    message = "; ".join(messages)
    return JSONResponse(
        status_code=422,
        content={
            "code": "VALIDATION_ERROR",
            "message": message,
            "request_id": request_id
        }
    )

@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    request_id = str(uuid.uuid4())
    code = "API_ERROR"
    message = str(exc.detail)
    if isinstance(exc.detail, dict):
        code = exc.detail.get("code", "API_ERROR")
        message = exc.detail.get("message", message)
    else:
        if exc.status_code == 401:
            code = "UNAUTHORIZED"
        elif exc.status_code == 403:
            code = "FORBIDDEN"
        elif exc.status_code == 409:
            code = "CONFLICT"
        elif exc.status_code == 404:
            code = "NOT_FOUND"
        elif exc.status_code == 422:
            code = "CSRF_INVALID"
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "code": code,
            "message": message,
            "request_id": request_id
        }
    )

@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    request_id = str(uuid.uuid4())
    logger.error("Unhandled exception: %s", str(exc), exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "code": "INTERNAL_ERROR",
            "message": "An internal server error occurred.",
            "request_id": request_id
        }
    )

app.include_router(router, prefix="/api/v1")
app.include_router(dq_router, prefix="/api/v1")
app.include_router(data_access_router)

@app.get("/health", tags=["System"])
async def health():
    return {"status": "ok", "env": settings.app_env}

@app.get("/ready", tags=["System"])
async def ready():
    try:
        with Session(get_engine()) as session:
            session.execute(text("SELECT 1"))
        return {"status": "ready", "database": "connected"}
    except Exception as e:
        logger.error("Ready check database connection failed: %s", e, exc_info=True)
        detail = "Service unavailable: Database connection failed."
        if settings.app_env == "local":
            detail += f" {str(e)}"
        raise HTTPException(status_code=503, detail=detail)
