import logging
import os

import httpx

# from google.cloud import run_v2 (moved inside function to prevent ImportError on local)

logger = logging.getLogger(__name__)

def dispatch_cloud_run_job(job_id: str, job_type: str) -> bool:
    """
    Trigger Cloud Run Job execution asynchronously using Run API v2.
    If APP_ENV is 'local' or 'development', delegates execution to a local worker container instead.
    """
    env = os.getenv("APP_ENV", "production")

    # 1. Local Fallback (Mocking Cloud Run Job)
    if env in ["local", "development"]:
        logger.info(f"Local environment detected. Dispatching job {job_id} to local worker.")
        try:
            worker_url = os.getenv("LOCAL_WORKER_URL", "http://worker:8001/run")
            response = httpx.post(f"{worker_url}?job_id={job_id}&job_type={job_type}", timeout=5.0)
            response.raise_for_status()
            logger.info("Local worker successfully triggered.")
            return True
        except Exception as e:
            logger.error(f"Failed to trigger local worker: {e}")
            return False

    # 2. Actual Cloud Run Job Dispatch
    project_id = os.getenv("GOOGLE_CLOUD_PROJECT")
    region = os.getenv("GOOGLE_CLOUD_REGION", "asia-southeast1")
    job_name = os.getenv("CLOUD_RUN_JOB_NAME", "ridepulse-worker")

    if not project_id:
        logger.warning("GOOGLE_CLOUD_PROJECT not set, skipping Cloud Run dispatch.")
        return False

    try:
        from google.cloud import run_v2
    except ImportError:
        logger.error("google-cloud-run library not installed. Cannot dispatch to Google Cloud.")
        return False

    client = run_v2.JobsClient()
    name = f"projects/{project_id}/locations/{region}/jobs/{job_name}"

    try:
        request = run_v2.RunJobRequest(
            name=name,
            overrides={
                "container_overrides": [
                    {
                        "env": [
                            {"name": "RUN_JOB_ID", "value": job_id},
                            {"name": "RUN_JOB_TYPE", "value": job_type}
                        ]
                    }
                ]
            }
        )
        client.run_job(request=request)
        logger.info(f"Dispatched Cloud Run Job {name} for task {job_id}")
        return True
    except Exception as e:
        logger.error(f"Failed to dispatch Cloud Run Job: {e}")
        return False
