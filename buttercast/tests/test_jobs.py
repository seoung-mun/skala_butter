import time


def test_failed_training_stage_is_not_left_running(tmp_path):
    from buttercast.operations import Operations
    from buttercast.store import Store

    store = Store(tmp_path)
    ops = Operations(store)
    def fail(job_id):
        ops.event(job_id, "train", "running")
        raise ValueError("fixture missing training input")
    ops.submit("training", fail)
    for _ in range(100):
        if ops.jobs()[0]["status"] == "failed":
            break
        time.sleep(.01)
    ops.executor.shutdown(wait=True)
    latest = {r["stage"]: r for r in store.read("stages")}
    assert latest["train"]["status"] == "failed"
    assert "fixture missing" in latest["train"]["error"]


def test_cancel_request_is_persistent_and_rejects_finished_jobs(tmp_path):
    import pytest

    from buttercast.operations import Operations
    from buttercast.store import Store

    ops = Operations(Store(tmp_path))
    ops.store.append("jobs", dict(id="queued", status="queued", kind="training", queued_at="2026-01-01"))
    assert ops.request_cancel("queued")["status"] == "cancel_requested"
    assert ops.store.latest("cancel_requests")["job_id"] == "queued"
    ops.store.append("jobs", dict(id="done", status="succeeded", kind="training", queued_at="2026-01-01"))
    with pytest.raises(ValueError, match="종료"):
        ops.request_cancel("done")
    ops.executor.shutdown(wait=True)
