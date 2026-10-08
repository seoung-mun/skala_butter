from fastapi.testclient import TestClient

from buttercast.app import create_app
from buttercast.store import Store


def test_restart_marks_interrupted_job_and_each_running_stage_failed(tmp_path):
    store = Store(tmp_path)
    store.append("jobs", dict(id="interrupted", kind="training", status="running", queued_at="2026-01-01",
                              queued_ts=1, started_ts=2))
    store.append("stages", dict(job_id="interrupted", stage="train", status="running", at="2026-01-01"))
    with TestClient(create_app(tmp_path)) as client:
        assert client.get("/api/jobs/interrupted").json()["status"] == "failed"
        assert store.latest("stages")["stage"] != "train" or store.latest("stages")["status"] == "failed"
        latest_train = [row for row in store.read("stages") if row["stage"] == "train"][-1]
        assert latest_train["status"] == "failed"
        assert "재시작" in latest_train["error"]


def test_incompatible_data_is_blocked_before_loading_a_model(tmp_path):
    import pytest

    from buttercast.operations import Operations
    from buttercast.pipeline import Pipeline

    store = Store(tmp_path)
    ops = Operations(store)
    store.append("datasets", dict(rows=[dict(unit="USD/lb", source="different_market", interval_days=7)]))
    store.append("models", dict(version="eu", synthetic=False, validation_passed=True,
                                unit="EUR/100kg", interval_days=7, source="European Commission MMO"))
    with pytest.raises(ValueError, match="계약"):
        Pipeline(ops).deploy("eu")
    assert not store.read("deployments")
    ops.executor.shutdown(wait=True)


def test_retry_requires_terminal_failure_and_original_saved_input(tmp_path):
    import pytest

    from buttercast.operations import Operations
    from buttercast.pipeline import Pipeline

    ops = Operations(Store(tmp_path))
    ops.store.append("jobs", dict(id="running", kind="training", status="running", queued_at="2026-01-01"))
    with pytest.raises(ValueError, match="실패"):
        Pipeline(ops).retry("running")
    ops.store.append("jobs", dict(id="failed", kind="training", status="failed", queued_at="2026-01-01"))
    with pytest.raises(ValueError, match="입력"):
        Pipeline(ops).retry("failed")
    ops.executor.shutdown(wait=True)
