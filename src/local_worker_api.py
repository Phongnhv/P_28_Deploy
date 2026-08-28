import os
import subprocess
import sys

from fastapi import FastAPI, HTTPException

app = FastAPI()

def run_job(job_id: str, job_type: str) -> bool:
    """
    Mocks a Cloud Run Job execution by spawning a subprocess of the worker module.
    """
    env = os.environ.copy()
    env["RUN_JOB_ID"] = job_id
    env["RUN_JOB_TYPE"] = job_type

    # Replace 'src.worker' with your actual worker entrypoint module
    print(f"[LocalWorker] Starting Job {job_id} ({job_type})...")
    try:
        # Popen hands the durable job to an independent worker process.  A
        # FastAPI BackgroundTask would die with the API process and could leave
        # a job stuck in PENDING/RUNNING forever.
        subprocess.Popen([sys.executable, "-m", "src.worker"], env=env, close_fds=True)
        return True
    except Exception as e:
        print(f"[LocalWorker] Error executing Job {job_id}: {e}")
        return False


@app.post("/run")
def trigger_job(job_id: str, job_type: str):
    if not run_job(job_id, job_type):
        raise HTTPException(status_code=503, detail="Local worker process could not be started")
    return {"status": "started", "job_id": job_id}
