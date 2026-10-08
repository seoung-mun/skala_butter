"""Separate training worker: runs the jobs an API started with JOB_RUNNER=worker only queued.

    BUTTERCAST_RUNTIME=runtime/aiops python -m buttercast.worker

The API and the worker share the CSV store (cross-process lock in Store). The API keeps serving and monitoring;
the worker trains, gates, deploys and runs experiments. A promotion moves the `deployments` pointer in the store;
the API reads that pointer on every request and loads the new version on first use (versions are immutable, so
its in-memory cache never needs invalidating).
"""
import fcntl
import logging
import os
import time
from pathlib import Path

from .app import recover_all
from .experiments import run_experiment
from .operations import Operations
from .pipeline import Pipeline
from .store import Store

LOGGER = logging.getLogger("buttercast.worker")


def work_for(ops, pipeline, job):
    """Rebuild the job's work from what the API stored (closures cannot cross processes)."""
    spec = job.get("spec") or {}
    if spec.get("type") == "train":
        inputs = next(r for r in reversed(ops.store.read("training_inputs")) if r["job_id"] == job["id"])
        return lambda job_id: pipeline.train_sync(job_id, inputs["version"], inputs["rows"], inputs["trigger_id"],
                                                  inputs["epochs"], inputs.get("max_seconds", 300))
    if spec.get("type") == "experiment":
        dataset = next(d for d in reversed(ops.store.read("datasets")) if d["version"] == spec["dataset_version"])
        return lambda job_id: run_experiment(ops, job_id, dataset["rows"], **spec["params"])
    raise ValueError(f"워커가 실행할 수 없는 작업입니다: {job['kind']} {spec}")


def run_once(ops, pipeline):
    """Run every queued job, oldest first. Returns how many ran."""
    queued = [job for job in reversed(ops.jobs()) if job["status"] == "queued"]
    for job in queued:
        ops.execute(job, work_for(ops, pipeline, job))
    return len(queued)


def main():
    logging.basicConfig(level=logging.INFO)
    root = Path(os.environ.get("BUTTERCAST_RUNTIME") or Path(__file__).resolve().parents[1]/"runtime"/"aiops").resolve()
    root.mkdir(parents=True, exist_ok=True)
    lock = (root/"worker.lock").open("a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
        raise RuntimeError("이 저장소에는 이미 워커가 실행 중입니다") from error
    ops = Operations(Store(root), runner="thread")
    pipeline = Pipeline(ops)
    recover_all(ops, root)  # jobs a previous worker left running
    LOGGER.info("worker started on %s", root)
    while True:
        (root/"worker.heartbeat").touch()
        if not run_once(ops, pipeline):
            time.sleep(1)


if __name__ == "__main__":
    main()
