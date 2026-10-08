"""User scenarios on the real EU data with real training. Each step is what a UI button calls."""
import time

import pytest
from fastapi.testclient import TestClient

from buttercast.app import create_app
from buttercast.data import RULES

pytestmark = pytest.mark.ml


def wait(client, job_id, timeout=300):
    deadline = time.monotonic()+timeout
    while time.monotonic() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("succeeded", "failed", "cancelled"):
            return job
        time.sleep(.1)
    raise TimeoutError(job_id)


def first_deploy(client):
    job = wait(client, client.post("/api/train", json={"epochs": 40}).json()["id"])
    assert job["status"] == "succeeded", job["error"]
    version = job["result"]["version"]
    assert job["result"]["gate"]["passed"] and job["result"]["promoted"], job["result"]["gate"]
    assert client.get("/api/models").json()["active"] == version  # gate pass → auto-deployed, no approval
    return version


def test_operator_first_deployment_and_buyer_forecast(tmp_path):
    """운영자: 학습 → 게이트 통과 → 자동 배포 / 구매 담당자: 4주 뒤 가격 예측 조회."""
    with TestClient(create_app(tmp_path)) as client:
        assert client.post("/api/predict").status_code == 409          # nothing deployed yet
        version = first_deploy(client)
        assert version == "v1"
        forecast = client.post("/api/predict").json()
        assert forecast["version"] == version and forecast["prediction"] > 0
        assert forecast["target_date"] > forecast["issued_at"][:10]
        assert forecast["korea"]["krw_per_kg"] > 0 and forecast["korea"]["passthrough_beta"] == 0.35
        adhoc = client.post("/api/predict", json={"prices": [500, 510, 505, 520, 530, 540]})
        assert adhoc.status_code == 200 and adhoc.json()["stored"] is False
        for bad in ([500]*5, [500, 0, 505, 520, 530, 540], [500, 510, 505, 520, 530, -1]):
            assert client.post("/api/predict", json={"prices": bad}).status_code == 422
        deployed = client.get("/api/models").json()["deployments"][-1]
        assert deployed["registry"] == {"name": "ButterCast_Predictor", "version": 1, "alias": "production"}
        log = "\n".join(client.get("/api/logs").json())
        assert "[GATE PASSED]" in log and f"auto_promote {version}" in log


def test_three_situations_normal_watch_finetune(tmp_path):
    """운영자: 정상 입력 → 아무 일 없음 / 이상 입력이지만 일시적 → 경보만 (파인튜닝은 다음 테스트)."""
    with TestClient(create_app(tmp_path)) as client:
        first_deploy(client)
        for kind, level in (("none", 0), ("level_ramp", 1)):
            job = client.post("/api/experiments", json={"kind": kind}).json()
            assert wait(client, job["id"], timeout=600)["status"] == "succeeded"
            result = client.get(f"/api/experiments/{job['id']}").json()
            assert (result["expected_level"], result["observed_level"]) == (level, level), result["level_weeks"]
            assert result["retraining"] == [] and result["normal_alarm_observations"] == 0


def test_operator_drift_response_retrains_promotes_and_rolls_back(tmp_path):
    """운영자: 드리프트 주입 → 경보 → 파인튜닝 → 게이트 → 운영 저장소로 들어와 자동 승격 → 이전 모델 다시 선택."""
    with TestClient(create_app(tmp_path)) as client:
        own = first_deploy(client)
        job = client.post("/api/experiments", json={"kind": "variance"}).json()   # starts from the serving model
        assert wait(client, job["id"], timeout=600)["status"] == "succeeded"
        result = client.get(f"/api/experiments/{job['id']}").json()
        assert result["observed_level"] == result["expected_level"] == 2
        assert result["retraining"], "drift must trigger at least one real retraining job"
        promoted = [r for r in result["retraining"] if r["promoted"]]
        assert promoted and all(r["gate"]["passed"] for r in promoted)
        for retrain in result["retraining"]:                       # fine-tune gate: cap + strictly better
            checks = {c["name"]: c for c in retrain["gate"]["checks"]}
            assert set(checks) == {"wape", "vs_champion"} and checks["vs_champion"]["op"] == "<"
            assert retrain["promoted"] == (checks["wape"]["passed"] and checks["vs_champion"]["passed"])
        assert result["replaced_model"] != result["initial_model"]
        assert result["rolled_back_model"] == result["initial_model"] and result["corruption_blocked"]
        log = "\n".join(result["aiops_log"])
        warn, info = log.index("[WARN]"), log.index("[INFO] retrain triggered")
        assert warn < info < log.index("[GATE PASSED]", info) < log.index("[OK] new_wape=", info)
        assert set(result["business"]) >= {"spot", "fixed_model", "adaptive_model"}
        listed = client.get("/api/models").json()
        adopted = [m for m in listed["models"] if m.get("from_experiment") == job["id"]]
        # Every model the run trained is registered (rejected ones too); the newest gate-passed one serves.
        assert [m["version"] for m in adopted] == result["adopted_models"] == [f"v{i+2}" for i in range(len(adopted))]
        assert len(adopted) == len(result["retraining"])
        newest = [m for m in adopted if m["validation_passed"]][-1]["version"]
        assert listed["active"] == newest and client.post("/api/predict").json()["version"] == newest
        chosen = client.post(f"/api/models/{own}/select")                  # operator picks another passed model
        assert chosen.status_code == 200 and client.post("/api/predict").json()["version"] == own
        assert len(result["sensitivity"]) == 9


def test_retrain_failing_gate_keeps_serving_model(tmp_path, monkeypatch):
    """운영자: 재학습 모델이 게이트를 못 넘으면 기존 모델이 계속 서빙된다."""
    with TestClient(create_app(tmp_path)) as client:
        version = first_deploy(client)
        monkeypatch.setitem(RULES["gate"]["finetune"], "max_wape_percent", 0.01)  # alarm retrain = fine-tune gate
        rows = client.get("/api/datasets").json()["rows"]
        retrain = client.app.state.ops.retrain_handler(rows, "manual-check")
        job = wait(client, retrain["id"])
        assert job["status"] == "succeeded" and job["result"]["promoted"] is False
        assert job["result"]["gate"]["passed"] is False
        failed = [c["name"] for c in job["result"]["gate"]["checks"] if not c["passed"]]
        assert "wape" in failed  # a single failing check is enough to block deployment
        assert client.get("/api/health").json()["active_model"] == version
        assert client.post("/api/predict").json()["version"] == version
        assert "[FAIL]" in "\n".join(client.get("/api/logs").json())


def test_lazy_and_eager_loading(tmp_path):
    """Lazy는 첫 요청 때, Eager는 시작 때 모델을 올린다."""
    with TestClient(create_app(tmp_path)) as client:
        first_deploy(client)
    with TestClient(create_app(tmp_path, loading_mode="lazy")) as client:
        assert client.get("/api/health").json()["model_loaded"] is False
        client.post("/api/predict")
        health = client.get("/api/health").json()
        assert health["model_loaded"] is True and health["model_load_seconds"] > 0
    with TestClient(create_app(tmp_path, loading_mode="eager")) as client:
        health = client.get("/api/health").json()
        assert health["loading_mode"] == "eager" and health["model_loaded"] is True
    log = "\n".join((tmp_path/"aiops.log").read_text().splitlines())
    assert "[lazy] model" in log and "[eager] model" in log
    with pytest.raises(ValueError, match="LOADING_MODE"):
        create_app(tmp_path, loading_mode="sometimes")


def test_challenger_vs_champion_and_automatic_rollbacks(tmp_path, monkeypatch):
    """운영자: 재학습 모델은 운영 모델보다 나쁘면 탈락 / 교체 직후 확인 실패 → 자동 롤백 / 서빙 중 오류 → 자동 롤백."""
    with TestClient(create_app(tmp_path)) as client:
        champion = first_deploy(client)
        ops = client.app.state.ops
        v1_wape = client.get("/api/models").json()["models"][0]["gate"]["value"]

        def retrain(name):
            # Plain training jobs: drift-triggered ones would hit the 91-day cooldown on the same observation.
            job = wait(client, client.post("/api/train", json={"epochs": 40}).json()["id"])
            assert job["status"] == "succeeded", (name, job["error"])
            return job["result"]

        # 1) The champion was refit on every week, so the same data has no week it hasn't seen: no fair
        #    comparison, recorded as not applicable instead of a rigged win or loss.
        same_data = retrain("champion-check")
        vs = next(c for c in same_data["gate"]["checks"] if c["name"] == "vs_champion")
        assert vs["champion"] == champion and vs["applicable"] is False and vs["weeks"] == 0
        assert client.get("/api/models").json()["models"][0]["refit"] is True and v1_wape > 0
        assert client.post("/api/models/" + champion + "/select").status_code == 200   # back to v1 for step 2

        # 2) Passes the gate, but the post-swap canary fails → straight back to the champion.
        monkeypatch.setitem(RULES["gate"], "max_wape_vs_champion", 10.0)
        monkeypatch.setitem(RULES["post_deploy"], "max_change_pct", -1.0)  # any change counts as a jump
        canary = retrain("canary-check")
        assert canary["gate"]["passed"] and canary["promoted"] is False, canary["gate"]["checks"]
        deployments = client.get("/api/models").json()["deployments"]
        assert [d["action"] for d in deployments[-2:]] == ["auto_promote", "auto_rollback"]
        assert deployments[-1]["version"] == champion

        # 3) Promoted for real, then its file breaks while serving → 503 once, champion serves again.
        monkeypatch.setitem(RULES["post_deploy"], "max_change_pct", 10.0)
        promoted = retrain("serving-check")
        assert promoted["promoted"] and client.get("/api/health").json()["active_model"] == promoted["version"]
        (tmp_path/"models"/promoted["version"]/"lstm.keras").write_bytes(b"broken")
        ops.cache.pop(promoted["version"], None)
        failed = client.post("/api/predict")
        assert failed.status_code == 503 and champion in failed.json()["detail"]
        assert client.post("/api/predict").json()["version"] == champion
        log = "\n".join(client.get("/api/logs").json())
        assert log.count("[ROLLBACK]") == 2


def test_worker_process_runs_jobs_the_api_only_queued(tmp_path):
    """학습 워커 분리: API(JOB_RUNNER=worker)는 작업을 기록만 하고, 워커가 꺼내 학습·게이트·배포한다.
    API는 다음 요청에서 바뀐 운영 버전을 읽어 그 모델로 예측한다(캐시 갱신)."""
    from buttercast.operations import Operations
    from buttercast.pipeline import Pipeline
    from buttercast.store import Store
    from buttercast.worker import run_once

    with TestClient(create_app(tmp_path, job_runner="worker")) as client:
        job = client.post("/api/train", json={"epochs": 40}).json()
        time.sleep(.5)
        assert client.get(f"/api/jobs/{job['id']}").json()["status"] == "queued"   # API does not train
        worker = Operations(Store(tmp_path))                                       # the worker process' view
        assert run_once(worker, Pipeline(worker)) == 1
        worker.executor.shutdown(wait=True)
        done = client.get(f"/api/jobs/{job['id']}").json()
        assert done["status"] == "succeeded" and done["result"]["promoted"]
        assert client.post("/api/predict").json()["version"] == done["result"]["version"]
        health = client.get("/api/health").json()
        assert health["job_runner"] == "worker" and health["worker"]["alive"] is False   # no heartbeat in a test
