import os
import subprocess
import sys

from fastapi import BackgroundTasks, FastAPI

app = FastAPI()

def run_job(job_id: str, job_type: str):
    """
    Mocks a Cloud Run Job execution by spawning a subprocess of the worker module.
    """
    env = os.environ.copy()
    env["RUN_JOB_ID"] = job_id
    env["RUN_JOB_TYPE"] = job_type

    # Replace 'src.worker' with your actual worker entrypoint module
    print(f"[LocalWorker] Starting Job {job_id} ({job_type})...")
    try:
        subprocess.run([sys.executable, "-m", "src.worker"], env=env, check=False)
        print(f"[LocalWorker] Job {job_id} finished.")
    except Exception as e:
        print(f"[LocalWorker] Error executing Job {job_id}: {e}")

@app.post("/run")
def trigger_job(job_id: str, job_type: str, background_tasks: BackgroundTasks):
    background_tasks.add_task(run_job, job_id, job_type)
    return {"status": "started", "job_id": job_id}
