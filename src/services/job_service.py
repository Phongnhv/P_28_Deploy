from sqlalchemy.orm import Session
from src.models.job import JobModel
from src.services.rule_store import get_engine
import uuid

def create_job(job_type: str, idempotency_key: str, linked_entity: str = None) -> tuple[JobModel, bool]:
    with Session(get_engine()) as session:
        # Idempotency check 
        existing = session.query(JobModel).filter_by(idempotency_key=idempotency_key).first()
        if existing:
            return existing, False # False means collision
            
        new_job = JobModel(
            id=str(uuid.uuid4()),
            type=job_type,
            idempotency_key=idempotency_key,
            linked_entity=linked_entity,
            status='PENDING'
        )
        session.add(new_job)
        session.commit()
        session.refresh(new_job)
        return new_job, True

def update_job_status(job_id: str, status: str, error: str = None) -> JobModel:
    with Session(get_engine()) as session:
        job = session.query(JobModel).filter_by(id=job_id).first()
        if job:
            job.status = status
            if error:
                job.error = error
            session.commit()
            session.refresh(job)
        return job

def get_job(job_id: str) -> JobModel:
    with Session(get_engine()) as session:
        return session.query(JobModel).filter_by(id=job_id).first()
