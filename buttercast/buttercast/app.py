import asyncio
import fcntl
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import date, timedelta
from pathlib import Path
from typing import Annotated, Literal

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import data, korea, models
from .data import RULES
from .experiments import KINDS, run_experiment
from .metrics import metric, service_summary, stamp
from .operations import Operations
from .pipeline import Pipeline
from .store import Store

STATIC = Path(__file__).parent/"static"
LOGGER = logging.getLogger(__name__)


class TrainRequest(BaseModel):
    epochs: int = Field(20, ge=1, le=100)
    max_seconds: float = Field(300, ge=.1, le=3600)


class RetryRequest(BaseModel):
    max_seconds: float | None = Field(None, ge=.1, le=3600)


class ExperimentRequest(BaseModel):
    kind: Literal[KINDS] = Field("variance", description="none 정상 입력 · level_ramp 이상 입력(파인튜닝 X) · variance 이상 입력(파인튜닝)")
    epochs: int = Field(20, ge=1, le=100)
    normal: int = Field(13, ge=1, le=52)
    changed: int = Field(26, ge=2, le=104)
    recovery: int = Field(26, ge=1, le=104)
    factor: float | None = Field(None, gt=0, le=5, description="생략하면 rules.json 기본값")


class PredictRequest(BaseModel):
    prices: Annotated[list[Annotated[float, Field(gt=0, allow_inf_nan=False)]],
                      Field(min_length=models.SEQUENCE, max_length=models.SEQUENCE)] | None = Field(
        None, description=f"최근 {models.SEQUENCE}주 연속 주간 가격(과거→최근). 생략하면 현재 데이터셋의 최근 관측 사용")


def recover_all(ops, root):
    """Jobs left `running` by a process that died are marked failed (main store and experiment stores)."""
    ops.recover_interrupted()
    for directory in (root / "experiments").glob("*"):
        if directory.is_dir():
            child = Operations(Store(directory))
            child.recover_interrupted()
            child.executor.shutdown(wait=True)


def worker_status(root):
    beat = root/"worker.heartbeat"
    seen = time.time()-beat.stat().st_mtime if beat.exists() else None
    return dict(alive=seen is not None and seen < 10, last_seen_seconds=seen)


def create_app(root=None, loading_mode=None, job_runner=None):
    # Absolute: MLflow artifact locations are file URIs, which cannot be relative.
    root = Path(root or os.environ.get("BUTTERCAST_RUNTIME") or Path(__file__).resolve().parents[1]/"runtime"/"aiops").resolve()
    loading_mode = loading_mode or os.environ.get("LOADING_MODE", "lazy")
    if loading_mode not in ("lazy", "eager"):
        raise ValueError("LOADING_MODE는 lazy 또는 eager만 허용합니다")
    job_runner = job_runner or os.environ.get("JOB_RUNNER", "thread")
    store = Store(root)
    ops = Operations(store, runner=job_runner)
    pipeline = Pipeline(ops)
    ops.retrain_handler = lambda rows, token: pipeline.submit_train(rows, trigger_id=token)

    async def monitor():
        while True:
            await asyncio.sleep(5)
            try:
                await asyncio.to_thread(ops.collect)
            except Exception as error:
                LOGGER.exception("Metrics collection failed")
                # Health endpoints expose failure; never report stale values as current.
                app.state.collection_error = str(error)
            else:
                app.state.collection_error = None

    @asynccontextmanager
    async def lifespan(app):
        lock = (root/"server.lock").open("a")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            lock.close()
            raise RuntimeError("CSV 저장소는 단일 서버 프로세스만 사용할 수 있습니다") from error
        if not ops.dataset():
            rows = data.bundled_rows()
            store.append("datasets", dict(version=data.version(rows), rows=rows, imported_at=stamp(), source="bundled_audit"))
            ops.event(data.version(rows), "ingest", "succeeded")
        if job_runner == "thread":  # in worker mode the worker owns running jobs and recovers them itself
            recover_all(ops, root)
        app.state.collection_error = None
        if loading_mode == "eager" and ops.active():
            ops.bundle(ops.active())
            ops.log("INFO", f"[eager] model {ops.active()} loaded in {ops.loads[ops.active()]:.3f}s at startup")
        ops.collect()
        ops.event("startup", "monitor", "succeeded")
        app.state.ready_seconds = time.perf_counter()-app.state.created
        task = asyncio.create_task(monitor())
        try:
            yield
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass  # Expected task cancellation during explicit server shutdown.
            ops.executor.shutdown(wait=True)
            fcntl.flock(lock, fcntl.LOCK_UN)
            lock.close()

    app = FastAPI(title="ButterCast", lifespan=lifespan)
    app.state.store, app.state.ops = store, ops
    app.state.created = time.perf_counter()

    @app.middleware("http")
    async def record_request(request: Request, call_next):
        start = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            path = request.url.path
            category = "experiment" if path.startswith("/api/experiments") else "business" if request.method == "POST" and path.startswith("/api/") else "observation"
            store.append("requests", dict(ts=time.time(), path=path, method=request.method,
                                          category=category, status=status, latency_ms=(time.perf_counter()-start)*1000))

    @app.get("/")
    def index():
        return FileResponse(STATIC/"index.html")

    @app.get("/api/health")
    def health():
        active = ops.active()
        return dict(status="collection_error" if app.state.collection_error else "running",
                    error=app.state.collection_error, active_model=active,
                    model_loaded=active in ops.cache, loading_mode=loading_mode,
                    model_load_seconds=ops.loads.get(active), startup_seconds=app.state.ready_seconds,
                    job_runner=job_runner, worker=worker_status(root) if job_runner == "worker" else None)

    @app.get("/api/datasets")
    def datasets():
        ds = ops.dataset()
        return dict(ds, quality=data.quality(ds["rows"]), schema=list(ds["rows"][0]))

    @app.post("/api/datasets/import")
    async def ingest(file: UploadFile = File(...)):
        raw = await file.read(5*1024*1024+1)
        if len(raw) > 5*1024*1024:
            raise HTTPException(413, "CSV는 5 MiB 이하만 허용합니다")
        try:
            rows = data.parse_weekly_prices(raw.decode("utf-8-sig"))
        except (ValueError, UnicodeDecodeError) as error:
            ops.alert("import", "data_quality", str(error), "error")
            raise HTTPException(422, str(error)) from error
        ds = dict(version=data.version(rows), rows=rows, imported_at=stamp(), source="team_provided_origin_unverified")
        store.append("datasets", ds)
        ops.alert("import", "data_quality", "CSV 검증 통과", status="resolved")
        ops.event(ds["version"], "ingest", "succeeded")
        ops.collect()
        return dict(version=ds["version"], rows=len(rows))

    @app.get("/api/metrics/series")
    def metric_series(minutes: int = Query(60, ge=5, le=1440)):
        """Per-minute latency, throughput and 5xx count of business requests (the dashboard's service charts)."""
        now = int(time.time()//60)
        bins = {minute: [] for minute in range(now-minutes+1, now+1)}
        for row in store.read("requests"):
            minute = int(row["ts"]//60)
            if row["category"] == "business" and minute in bins:
                bins[minute].append(row)
        return [dict(minute=minute*60, requests=len(rows), errors=sum(r["status"] >= 500 for r in rows),
                     latency_mean=sum(r["latency_ms"] for r in rows)/len(rows) if rows else None)
                for minute, rows in bins.items()]

    @app.get("/api/metrics")
    def metrics(window: int = Query(300)):
        if window not in (300, 3600, 21600, 86400):
            raise HTTPException(422, "허용 창: 300, 3600, 21600, 86400초")
        snapshot = store.latest("metrics")
        return dict(snapshot, service=service_summary(store.read("requests"), window),
                    collection_error=app.state.collection_error,
                    stale=time.time()-snapshot["ts"] > 15,
                    model_history=[r["scores"] for r in store.read("model_evaluations") if r.get("version") == ops.active()][-60:],
                    history=store.read("metrics")[-60:])

    @app.get("/api/alerts")
    def alerts():
        return list(reversed(store.read("alerts")[-100:]))

    @app.get("/api/jobs")
    def jobs():
        stages = store.read("stages")
        return dict(jobs=[j for j in ops.jobs() if j["kind"] != "simulation"], stages=[r for r in stages if r["stage"] != "simulation"][-100:], stage_states=list({r["stage"]: r for r in stages}.values()))

    @app.get("/api/jobs/{job_id}")
    def job(job_id: str):
        found = next((j for j in ops.jobs() if j["id"] == job_id and j["kind"] != "simulation"), None)
        if found is None:
            raise HTTPException(404, "작업을 찾을 수 없습니다")
        return found

    @app.post("/api/train", status_code=202)
    def train(request: TrainRequest):
        rows = [r for r in ops.dataset()["rows"] if r["available_on"] <= date.today().isoformat()]
        try:
            models.prepare(rows, interval_days=rows[0]["interval_days"])
        except ValueError as error:
            ops.alert("train_preflight", "training", str(error))
            raise HTTPException(422, str(error)) from error
        return pipeline.submit_train(rows, epochs=request.epochs, max_seconds=request.max_seconds)

    @app.post("/api/jobs/{job_id}/retry", status_code=202)
    def retry_job(job_id: str, request: RetryRequest):
        try:
            return pipeline.retry(job_id, request.max_seconds)
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    @app.post("/api/jobs/{job_id}/cancel", status_code=202)
    def cancel_job(job_id: str):
        try:
            return ops.request_cancel(job_id)
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    @app.get("/api/models")
    def model_list():
        return dict(active=ops.active(), models=store.read("models"), deployments=store.read("deployments"),
                    retrain_candidates=store.read("retrain_candidates"))

    def deploy(version, action):
        try:
            result = pipeline.deploy(version, action)
        except KeyError as error:
            raise HTTPException(404, str(error)) from error
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        ops.collect()
        return result

    @app.post("/api/models/{version}/select")
    def select(version: str):
        """Serve any gate-passed model (own training or adopted from an experiment)."""
        return deploy(version, "select")

    @app.post("/api/models/{version}/rollback")
    def rollback(version: str):
        return deploy(version, "rollback")

    def serving_bundle(version):
        if version not in ops.cache:
            ops.bundle(version)
            ops.log("INFO", f"[{loading_mode}] model {version} loaded in {ops.loads[version]:.3f}s on first request")
        return ops.cache[version]

    def serve(version, prices):
        """A serving failure of the deployed model rolls back to the one it replaced, then fails this request loudly.
        Inputs are already validated by Pydantic, so an exception here is the model's, not the caller's."""
        try:
            return serving_bundle(version).predict(prices)
        except Exception as error:
            latest = store.latest("deployments")
            if latest["version"] != version or not latest.get("previous") or latest["action"] == "auto_rollback":
                raise
            ops.log("ROLLBACK", f"{version} serving error: {error} → back to {latest['previous']}")
            ops.cache.pop(version, None)
            pipeline.deploy(latest["previous"], "auto_rollback")
            raise HTTPException(503, f"{version} 예측 실패로 {latest['previous']}(으)로 자동 롤백했습니다. "
                                     f"다시 요청하세요: {error}") from error

    def korea_view(estimates):
        # The Korean pass-through was calibrated on the EU EUR/100kg series only; other datasets get none.
        return korea.estimate(estimates["prediction"]) if ops.dataset()["rows"][0]["unit"] == "EUR/100kg" else None

    @app.post("/api/predict")
    def predict(request: PredictRequest | None = None):
        version = ops.active()
        if version is None:
            raise HTTPException(409, "운영 모델이 없습니다. 학습하면 게이트(검증 WAPE)를 통과한 모델이 자동 배포됩니다.")
        if request is not None and request.prices is not None:
            # Ad-hoc forecast for a caller-supplied sequence; not stored as an operational prediction.
            start = time.perf_counter()
            estimates = serve(version, request.prices)
            return dict(version=version, unit=ops.dataset()["rows"][0]["unit"], horizon_days=28, stored=False,
                        inference_ms=(time.perf_counter()-start)*1000, korea=korea_view(estimates), **estimates)
        rows = [r for r in ops.dataset()["rows"] if r["available_on"] <= date.today().isoformat()]
        last = rows[-models.SEQUENCE:]
        if len(last) < models.SEQUENCE or any((date.fromisoformat(b["date"])-date.fromisoformat(a["date"])).days != rows[0]["interval_days"] for a, b in zip(last, last[1:])):
            raise HTTPException(422, "최근 입력에 주간 결측 구간이 있습니다")
        target = date.fromisoformat(last[-1]["available_on"])+timedelta(days=28)
        if target <= date.today():
            raise HTTPException(409, "입력이 오래되어 예측 목표일이 이미 지났습니다. 새 데이터를 적재하세요.")
        start = time.perf_counter()
        estimates = serve(version, [r["midpoint"] for r in last])
        result = dict(id=uuid.uuid4().hex[:12], issued_at=stamp(), target_date=target.isoformat(), version=version,
                      data_version=ops.dataset()["version"], horizon_days=28, actual=None, actual_known_at=None,
                      inference_ms=(time.perf_counter()-start)*1000, korea=korea_view(estimates), **estimates)
        store.append("predictions", result)
        ops.event(result["id"], "predict", "succeeded")
        return result

    @app.get("/api/predictions")
    def predictions():
        return store.read("predictions")[-100:]

    @app.get("/api/logs")
    def logs(lines: int = Query(200, ge=1, le=2000)):
        path = root/"aiops.log"
        return path.read_text(encoding="utf-8").splitlines()[-lines:] if path.exists() else []

    @app.post("/api/experiments", status_code=202)
    def experiments(request: ExperimentRequest):
        rows = ops.dataset()["rows"]
        if any(row["synthetic"] is not False for row in rows):
            raise HTTPException(409, "실험의 기준 데이터는 원본 확인된 EU 가격을 선택하세요")
        if ops.active() is None:
            raise HTTPException(409, "운영 모델이 없습니다: 먼저 학습해 운영 모델을 만든 뒤 실험하세요")
        params = dict(epochs=request.epochs, normal=request.normal, changed=request.changed,
                      recovery=request.recovery, factor=request.factor, kind=request.kind)
        return ops.submit("experiment", lambda job_id: run_experiment(ops, job_id, rows, **params),
                          spec=dict(type="experiment", dataset_version=ops.dataset()["version"], params=params))

    @app.get("/api/experiments")
    def experiment_results():
        return store.read("experiments")

    @app.get("/api/experiments/{experiment_id}")
    def experiment_result(experiment_id: str):
        result = next((row for row in store.read("experiments") if row["id"] == experiment_id), None)
        if result is None:
            raise HTTPException(404, "아직 완료된 실험 결과 없음; 작업 상태를 확인하세요")
        return result

    @app.get("/api/system")
    def system():
        return dict(rules=RULES, job_runner=job_runner, worker=worker_status(root) if job_runner == "worker" else None,
                    storage="CSV · single process", sequence=models.SEQUENCE, horizon_days=28,
                    loading_mode=loading_mode, features=models.FEATURES,
                    minimum_training_rows=models.MIN_ROWS, model="lstm", model_source="local_bundle + MLflow registry",
                    monitor_interval_seconds=5, reference_observations=52, current_observations=13,
                    calibration_windows=20, service=RULES["service"],
                    collection_error=app.state.collection_error, resources=store.latest("metrics")["resources"],
                    uptime=metric(time.time()-app.state.started, "s", 1))

    app.state.started = time.time()
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
