from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException
from pydantic import BaseModel

from src.api.dependencies import verify_idempotency_key
from src.services.gcp_run import dispatch_cloud_run_job
from src.services.job_service import create_job

router = APIRouter(prefix="/api/v1/jobs", tags=["Jobs"])

class CreateJobRequest(BaseModel):
    type: str
    linked_entity: str = None

@router.post("", status_code=202)
def trigger_job(
    request: CreateJobRequest,
    background_tasks: BackgroundTasks,
    idempotency_key: str = Depends(verify_idempotency_key),
    x_correlation_id: str | None = Header(None, alias="X-Correlation-ID")
):
    job, created = create_job(
        job_type=request.type,
        idempotency_key=idempotency_key,
        linked_entity=request.linked_entity,
        correlation_id=x_correlation_id
    )

    if not created:
        raise HTTPException(status_code=409, detail="Idempotency key collision during creation")

    # Dispatch non-blocking
    background_tasks.add_task(dispatch_cloud_run_job, job.id, job.type)

    return {"job_id": job.id, "status": job.status, "message": "Job accepted"}

@router.get("/{job_id}")
def get_job_status_endpoint(job_id: str):
    from src.services.job_service import get_job
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    return {
        "job_id": job.id,
        "type": job.type,
        "status": job.status,
        "error": job.error,
        "attempt_count": job.attempt_count,
        "correlation_id": getattr(job, 'correlation_id', None),
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None
    }
