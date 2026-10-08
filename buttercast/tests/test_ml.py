from datetime import date, timedelta

import numpy as np
import pytest

pytestmark = pytest.mark.ml


def rows_fixture(n=160, seed=31):
    rng = np.random.default_rng(seed)
    prices = 5000*np.exp(np.cumsum(rng.normal(0, .02, n)))
    start = date(2010, 1, 3)
    return [dict(date=(start+timedelta(weeks=i)).isoformat(), available_on=(start+timedelta(weeks=i, days=7)).isoformat(),
                 midpoint=float(p), unit="EUR/100kg") for i, p in enumerate(prices)]


def test_log_return_bundle_roundtrip_gate_and_warm_start(tmp_path):
    from buttercast.models import FEATURES, Bundle, train_bundle

    rows = rows_fixture()
    base = tmp_path/"models"/"base"
    metadata = train_bundle(rows, base, epochs=3, synthetic=True, interval_days=7)
    assert metadata["feature_schema"] == FEATURES and metadata["mlflow_run_id"]
    assert set(metadata["comparisons"]["test"]) == {"lstm"}
    checks = {c["name"]: c for c in metadata["gate"]["checks"]}
    assert set(checks) == {"wape", "rmse_pct", "direction"}
    assert checks["direction"]["op"] == ">=" and 0 <= checks["direction"]["value"] <= 100
    assert metadata["gate"]["passed"] == all(c["passed"] for c in checks.values())
    assert metadata["epochs_run"] <= 3 and 1 <= metadata["best_epoch"] <= metadata["epochs_run"]
    prices = [r["midpoint"] for r in rows[-6:]]
    predicted = Bundle(base).predict(prices)
    assert set(predicted) == {"prediction"}
    # Level shift far outside training: returns-based inputs still give a proportional forecast.
    shifted = Bundle(base).predict([p*10 for p in prices])
    assert shifted["prediction"] == pytest.approx(predicted["prediction"]*10, rel=1e-5)
    tuned = train_bundle(rows, tmp_path/"models"/"tuned", epochs=2,
                         synthetic=True, interval_days=7, base=base)
    assert tuned["warm_start_from"] == "base" and tuned["scale"] == metadata["scale"]
    with pytest.raises(ValueError, match="연속 가격"):
        Bundle(base).predict(prices[:5])


def test_registry_alias_and_rollback_reuses_version(tmp_path):
    import shutil

    from fastapi.testclient import TestClient

    from buttercast.app import create_app
    from buttercast.models import train_bundle
    from buttercast.pipeline import Pipeline

    rows = rows_fixture()
    folder = tmp_path/"models"/"fixture"
    metadata = train_bundle(rows, folder, epochs=2, synthetic=True, interval_days=7)
    app = create_app(tmp_path)
    store = app.state.store
    store.append("models", dict(metadata, version="fixture"))
    with TestClient(app) as client:
        dataset = client.get("/api/datasets").json()["rows"][0]
        pipeline = Pipeline(app.state.ops)
        with pytest.raises(ValueError, match="변형 데이터"):
            pipeline.deploy("fixture")  # synthetic: never in production
        for version in ("eligible-1", "eligible-2"):
            shutil.copytree(folder, tmp_path/"models"/version)
            store.append("models", dict(metadata, version=version, synthetic=False, validation_passed=True,
                                        unit=dataset["unit"], source=dataset["source"],
                                        operational_validation_passed=True, artifact_health=dict(hashes=None)))
            store.append("bundle_calibrations", dict(version=version, input={}, unit=dataset["unit"], wape=[]))
            pipeline.deploy(version)
        assert client.post("/api/models/eligible-1/rollback").status_code == 200
        deployments = client.get("/api/models").json()["deployments"]
        assert [d["version"] for d in deployments] == ["eligible-1", "eligible-2", "eligible-1"]
        # Same MLflow run → the rollback re-points `production` to the existing registry version.
        assert deployments[-1]["registry"]["version"] == deployments[0]["registry"]["version"]
        assert client.get("/api/health").json()["active_model"] == "eligible-1"
