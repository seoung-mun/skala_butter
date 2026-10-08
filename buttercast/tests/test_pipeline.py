import pytest

from buttercast.operations import Operations
from buttercast.store import Store


def test_deployment_never_accepts_unknown_origin_or_missing_artifacts(tmp_path):
    from buttercast.pipeline import Pipeline

    store = Store(tmp_path)
    ops = Operations(store)
    pipeline = Pipeline(ops)
    store.append("models", dict(version="unknown", synthetic=None, validation_passed=True))
    with pytest.raises(ValueError, match="출처"):
        pipeline.deploy("unknown", acknowledge_degraded=True)
    store.append("models", dict(version="missing", synthetic=False, validation_passed=True))
    with pytest.raises(ValueError, match="파일"):
        pipeline.deploy("missing", acknowledge_degraded=True)
    assert not store.read("deployments")
    ops.executor.shutdown(wait=True)


def test_ks_saturated_boundary_is_explicit():
    from buttercast.metrics import drift

    stats = drift(list(range(65)), {"ks": [1.0]*20})
    assert stats["ks"]["status"] == "saturated"
    assert stats["ks"]["threshold"] == 1
    assert stats["ks"]["reason"]
