import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from src.models.database import JobModel
from src.services.rule_store import get_engine


def create_job(job_type: str, idempotency_key: str, linked_entity: str = None, correlation_id: str = None) -> tuple[JobModel, bool]:
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
            correlation_id=correlation_id or str(uuid.uuid4()),
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



def claim_job(job_id: str, lease_duration_seconds: int = 300) -> bool:
    """
    Acquires a lease on a job. Transitions PENDING or stale RUNNING to RUNNING.
    Returns True if successfully claimed.
    """
    now = datetime.now(UTC)
    with Session(get_engine()) as session:
        job = session.query(JobModel).filter_by(id=job_id).first()
        if not job:
            return False

        if job.status == 'RUNNING':
            lease_expires = getattr(job, 'lease_expires_at', None)
            if lease_expires and lease_expires.replace(tzinfo=UTC) < now:
                # Stale lease, we can reclaim it
                job.status = 'PENDING'
                job.error = "Lease expired, reclaimed by worker"
            else:
                return False

        if job.status in ['SUCCEEDED', 'COMPLETED', 'FAILED']:
            return False

        job.status = 'RUNNING'
        job.attempt_count = (job.attempt_count or 0) + 1
        if hasattr(job, 'lease_expires_at'):
            job.lease_expires_at = now + timedelta(seconds=lease_duration_seconds)
        session.commit()
        return True

def check_and_cleanup_stale_leases() -> int:
    """
    Finds running jobs whose leases have expired and marks them as FAILED_RETRYABLE.
    Returns the count of modified jobs.
    """
    now = datetime.now(UTC)
    with Session(get_engine()) as session:
        stale_jobs = session.query(JobModel).filter(
            JobModel.status == 'RUNNING',
            JobModel.lease_expires_at < now
        ).all()

        count = 0
        for job in stale_jobs:
            job.status = 'FAILED_RETRYABLE'
            job.error = "Job lease expired (worker did not report back)"
            count += 1

        if count > 0:
            session.commit()
        return count
