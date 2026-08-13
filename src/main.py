import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import text

from src.api.routes import dq_router, router
from src.api.jobs import router as jobs_router
from src.config import get_settings
from src.services.rule_store import init_db, get_engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    init_db()
    print(f"Starting {settings.app_name} in {settings.app_env} mode")
    yield
    print("Shutting down...")


app = FastAPI(
    title="AI20K Agent",
    description="AI Agent built with LangGraph",
    version="1.0.0",
    lifespan=lifespan,
)

settings = get_settings()

# 1. CORS Configuration
frontend_origin_env = os.getenv("FRONTEND_ORIGIN")
origins = settings.cors_origins.split(",")

if frontend_origin_env:
    origins.extend([o.strip() for o in frontend_origin_env.split(",") if o.strip()])

allow_origins = list(set(origins))

app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_credentials=True,
    allow_methods=["*"],  
    allow_headers=["*"],  
)

app.include_router(router, prefix="/api/v1")
app.include_router(dq_router, prefix="/api/v1")
app.include_router(jobs_router)

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
        raise HTTPException(status_code=503, detail=f"Service unavailable: Database connection failed. {str(e)}")
