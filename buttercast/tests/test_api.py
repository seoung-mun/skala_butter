from fastapi.testclient import TestClient


def test_api_real_data_empty_models_and_observation_exclusion(tmp_path):
    from buttercast.app import create_app

    with TestClient(create_app(tmp_path)) as client:
        data = client.get("/api/datasets").json()
        assert data["quality"]["rows"] == 1343
        assert not data["quality"]["gaps"]
        assert data["rows"][-1]["unit"] == "EUR/100kg"
        for _ in range(3):
            metrics = client.get("/api/metrics").json()
            assert metrics["service"]["request_count"]["value"] == 0
            assert metrics["model"]["rmse"]["value"] is None
        assert client.post("/api/predict").status_code == 409
        assert client.get("/api/metrics").json()["service"]["request_count"]["value"] == 1


def test_small_upload_fails_training_preflight_without_model(tmp_path):
    from buttercast.app import create_app

    with TestClient(create_app(tmp_path)) as client:
        assert client.get("/api/jobs").json()["jobs"] == []
        small = "week_ending,butter_price_usd_lb\n2026-09-19,1.5\n2026-09-26,1.6\n"
        assert client.post("/api/datasets/import", files={"file": ("small.csv", small.encode(), "text/csv")}).status_code == 200
        assert client.post("/api/train", json={}).status_code == 422
        assert client.get("/api/models").json()["active"] is None
        assert client.post("/api/models/missing/rollback").status_code == 404


def test_bad_upload_cannot_replace_data_and_error_is_recorded(tmp_path):
    from buttercast.app import create_app

    with TestClient(create_app(tmp_path)) as client:
        version = client.get("/api/datasets").json()["version"]
        result = client.post("/api/datasets/import", files={"file": ("bad.csv", b"price\n3", "text/csv")})
        assert result.status_code == 422
        assert client.get("/api/datasets").json()["version"] == version
        assert any(a["kind"] == "data_quality" for a in client.get("/api/alerts").json())


def test_generated_experiments_are_rejected_without_writing_results(tmp_path):
    from buttercast.app import create_app

    with TestClient(create_app(tmp_path)) as client:
        result = client.post("/api/experiments", json={"kind": "relation"})
        assert result.status_code == 422
        assert client.get("/api/experiments").json() == []
        assert client.get("/api/metrics").json()["service"]["request_count"]["value"] == 0


def test_two_server_instances_are_rejected(tmp_path):
    import pytest

    from buttercast.app import create_app

    with TestClient(create_app(tmp_path)):
        with pytest.raises(RuntimeError, match="단일"):
            with TestClient(create_app(tmp_path)):
                pass


def test_predict_body_contract_without_model(tmp_path):
    from buttercast.app import create_app

    with TestClient(create_app(tmp_path)) as client:
        assert client.post("/api/predict").status_code == 409            # no body: dataset forecast path
        assert client.post("/api/predict", json={}).status_code == 409   # empty JSON (UI) is the same path
        assert client.post("/api/predict", json={"prices": [1, 2]}).status_code == 422
        assert client.post("/api/predict", json={"prices": [1, 2, 3, 4, 5, -6]}).status_code == 422


def test_relative_runtime_env_is_resolved(tmp_path, monkeypatch):
    from buttercast.app import create_app

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BUTTERCAST_RUNTIME", "relative-store")
    app = create_app()
    assert app.state.store.root == (tmp_path/"relative-store").resolve() and app.state.store.root.is_absolute()


def test_experiment_needs_a_serving_model(tmp_path):
    """실험은 지금 운영 중인 모델로 한다: 운영 모델이 없으면 실행 전에 409, 상황은 3가지만 받는다."""
    from buttercast.app import create_app

    with TestClient(create_app(tmp_path)) as client:
        response = client.post("/api/experiments", json={"kind": "variance"})
        assert response.status_code == 409 and "운영 모델" in response.json()["detail"]
        assert client.post("/api/experiments", json={"kind": "mean"}).status_code == 422
        assert client.get("/api/jobs").json()["jobs"] == []


def test_service_metrics_latency_throughput_error_rate(tmp_path):
    """대시보드 서비스 지표: 업무 요청(POST)만 집계하고 분 단위 시계열을 준다(강의 Day3 지연시간·처리량·에러율)."""
    from buttercast.app import create_app

    with TestClient(create_app(tmp_path)) as client:
        for _ in range(3):
            assert client.post("/api/predict").status_code == 409   # no model yet: business request, 4xx
        client.get("/api/health")                                     # dashboard polling: not counted
        service = client.get("/api/metrics").json()["service"]
        assert service["request_count"]["value"] == 3 and service["error_rate"]["value"] == 0
        assert service["client_error_rate"]["value"] == 100 and service["latency_mean"]["value"] > 0
        series = client.get("/api/metrics/series?minutes=5").json()
        assert len(series) == 5 and sum(m["requests"] for m in series) == 3
        assert client.get("/api/system").json()["service"]["max_mean_latency_ms"] == 500
