import os
import logging
from google.cloud import run_v2

logger = logging.getLogger(__name__)

def dispatch_cloud_run_job(job_id: str, job_type: str) -> bool:
    """
    Trigger Cloud Run Job execution asynchronously using Run API v2.
    """
    project_id = os.getenv("GOOGLE_CLOUD_PROJECT")
    region = os.getenv("GOOGLE_CLOUD_REGION", "asia-southeast1")
    job_name = os.getenv("CLOUD_RUN_JOB_NAME", "ridepulse-worker")
    
    if not project_id:
        logger.warning("GOOGLE_CLOUD_PROJECT not set, skipping Cloud Run dispatch.")
        return False
        
    client = run_v2.JobsClient()
    name = f"projects/{project_id}/locations/{region}/jobs/{job_name}"
    
    try:
        # Override env vars for the specific execution
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
        operation = client.run_job(request=request)
        logger.info(f"Dispatched Cloud Run Job {name} for task {job_id}")
        return True
    except Exception as e:
        logger.error(f"Failed to dispatch Cloud Run Job: {e}")
        return False
