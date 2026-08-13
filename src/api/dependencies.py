from fastapi import Header, HTTPException
from sqlalchemy.orm import Session

from src.models.database import JobModel
from src.services.rule_store import get_engine


def verify_idempotency_key(idempotency_key: str = Header(..., alias="Idempotency-Key")):
    """
    Dependency to verify idempotency key header.
    Returns 409 Conflict immediately if the key is already used.
    """
    if not idempotency_key:
        raise HTTPException(status_code=400, detail="Idempotency-Key header is required")

    with Session(get_engine()) as session:
        existing_job = session.query(JobModel).filter_by(idempotency_key=idempotency_key).first()
        if existing_job:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "Request already processed or in progress",
                    "job_id": existing_job.id,
                    "status": existing_job.status
                }
            )
    return idempotency_key
