from fastapi import APIRouter, Depends, BackgroundTasks, HTTPException
from pydantic import BaseModel
from src.api.dependencies import verify_idempotency_key
from src.services.job_service import create_job
from src.services.gcp_run import dispatch_cloud_run_job

router = APIRouter(prefix="/api/v1/jobs", tags=["Jobs"])

class CreateJobRequest(BaseModel):
    type: str
    linked_entity: str = None

@router.post("", status_code=202)
def trigger_job(
    request: CreateJobRequest, 
    background_tasks: BackgroundTasks,
    idempotency_key: str = Depends(verify_idempotency_key)
):
    job, created = create_job(
        job_type=request.type, 
        idempotency_key=idempotency_key, 
        linked_entity=request.linked_entity
    )
    
    if not created:
        raise HTTPException(status_code=409, detail="Idempotency key collision during creation")
        
    # Dispatch non-blocking
    background_tasks.add_task(dispatch_cloud_run_job, job.id, job.type)
    
    return {"job_id": job.id, "status": job.status, "message": "Job accepted"}
