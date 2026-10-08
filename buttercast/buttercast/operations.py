import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import numpy as np
import psutil

from . import data, models
from .data import RULES
from .metrics import accuracy, drift, matured, metric, service_summary, stamp
from .monitoring import RETURN_UNIT, evaluate_input, weekly_returns

LOGGER = logging.getLogger(__name__)


class JobCancelled(RuntimeError):
    pass


def check_alert(store, key, exceeded, token, now, min_interval=0):
    previous = {r["key"]: r for r in store.read("alert_checks")}.get(key)
    if previous and (previous["token"] == token or now-previous.get("ts", 0) < min_interval):
        return None
    count = (previous["count"] if previous else 0)+1 if exceeded else 0
    store.append("alert_checks", dict(key=key, token=token, count=count, ts=now))
    return count


class Operations:
    def __init__(self, store, runner="thread"):
        """runner: "thread" runs jobs in this process; "worker" only queues them for `python -m buttercast.worker`."""
        if runner not in ("thread", "worker"):
            raise ValueError(f"JOB_RUNNER는 thread 또는 worker만 허용합니다: {runner}")
        self.store, self.runner = store, runner
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="buttercast-job")
        self.cache = {}
        self.loads = {}
        self.retrain_handler = None
        self.process = psutil.Process()
        self.process.cpu_percent()

    def dataset(self):
        return self.store.latest("datasets")

    def as_of(self):
        clock = self.store.latest("replay_clock")
        return clock["as_of"] if clock else date.today().isoformat()

    def active(self):
        entry = self.store.latest("deployments")
        return entry["version"] if entry else None

    def log(self, level, message):
        """Human-readable AIOps trail (WARN → INFO → OK) next to the CSV records."""
        with self.store.lock, (self.store.root/"aiops.log").open("a", encoding="utf-8") as file:
            file.write(f"{stamp()} [{level}] {message}\n")

    def bundle(self, version):
        if version not in self.cache:
            start = time.perf_counter()
            self.cache[version] = models.Bundle(self.store.root/"models"/version)
            self.loads[version] = time.perf_counter()-start
        return self.cache[version]

    def jobs(self):
        jobs = {}
        for row in self.store.read("jobs"):
            jobs[row["id"]] = row
        return sorted(jobs.values(), key=lambda x: x["queued_at"], reverse=True)

    def recover_interrupted(self):
        for job in self.jobs():
            if job["status"] not in ("queued", "running"):
                continue
            error = "서버 재시작으로 작업 중단; 수동 재실행 필요"
            self.store.append("jobs", dict(job, status="failed", error=error, ended_at=stamp(), ended_ts=time.time()))
            stages = {row["stage"]: row for row in self.store.read("stages") if row["job_id"] == job["id"]}
            for stage, record in stages.items():
                if record["status"] == "running":
                    self.event(job["id"], stage, "failed", error)
            self.event(job["id"], job["kind"], "failed", error)
            self.alert(f"job:{job['id']}", "job_failure", error, "error")

    def event(self, job, stage, status, error=None):
        self.store.append("stages", dict(job_id=job, stage=stage, status=status, error=error, at=stamp()))

    def request_cancel(self, job_id):
        job = next((row for row in self.jobs() if row["id"] == job_id), None)
        if job is None or job["status"] not in ("queued", "running"):
            raise ValueError("종료되었거나 없는 작업은 취소 불가")
        self.store.append("cancel_requests", dict(job_id=job_id, at=stamp()))
        return dict(job_id=job_id, status="cancel_requested")

    def check_cancel(self, job_id):
        if any(row["job_id"] == job_id for row in self.store.read("cancel_requests")):
            raise JobCancelled("취소 요청 확인: 모델 저장/배포 전에 학습 중단")

    def submit(self, kind, work, spec=None):
        """spec: what a worker process needs to rebuild `work` (closures cannot cross processes)."""
        job = dict(id=uuid.uuid4().hex[:12], kind=kind, status="queued", queued_at=stamp(),
                   queued_ts=time.time(), result=None, error=None, spec=spec)
        if self.runner == "worker" and spec is None:
            raise ValueError(f"워커 모드에서는 작업 명세(spec)가 필요합니다: {kind}")
        self.store.append("jobs", job)
        if self.runner == "thread":
            self.executor.submit(self.execute, job, work)
        return job

    def execute(self, job, work):
        kind = job["kind"]
        running = dict(job, status="running", started_at=stamp(), started_ts=time.time())
        self.store.append("jobs", running)
        self.event(job["id"], kind, "running")
        try:
            self.check_cancel(job["id"])
            result = work(job["id"])
        except Exception as error:
            terminal = "cancelled" if isinstance(error, JobCancelled) else "failed"
            if terminal == "failed":
                LOGGER.exception("Job %s failed", job["id"])
            else:
                LOGGER.info("Job %s cancelled: %s", job["id"], error)
            self.store.append("jobs", dict(running, status=terminal, ended_at=stamp(), ended_ts=time.time(), error=str(error)))
            stages = {r["stage"]: r for r in self.store.read("stages") if r["job_id"] == job["id"]}
            for stage, record in stages.items():
                if record["status"] == "running":
                    self.event(job["id"], stage, terminal, str(error))
            self.event(job["id"], kind, terminal, str(error))
            self.alert(f"job:{job['id']}", "job_failure", str(error), "error")
        else:
            self.store.append("jobs", dict(running, status="succeeded", ended_at=stamp(), ended_ts=time.time(), result=result))
            self.event(job["id"], kind, "succeeded")

    def alert(self, key, kind, message, severity="warning", status="open"):
        latest = {row["key"]: row for row in self.store.read("alerts")}.get(key)
        if latest and latest["status"] == status and latest["message"] == message:
            return
        self.store.append("alerts", dict(key=key, kind=kind, message=message, severity=severity,
                                         status=status, at=stamp()))
        level = "INFO" if status == "resolved" else "ERROR" if severity == "error" else "WARN"
        self.log(level, f"{kind} {status}: {message} ({key})")

    def model_metrics(self):
        version = self.active()
        observations = {r["date"]: r for r in self.dataset()["rows"]}
        records = []
        for original in self.store.read("predictions"):
            row = dict(original)
            actual = observations.get(row["target_date"])
            if actual:
                row.update(actual=actual["midpoint"], actual_known_at=actual["available_on"])
            records.append(row)
        resolved = matured(records, self.as_of(), version, 28)
        signature = data.version([dict(version=version), *resolved])
        cached = self.store.latest("model_evaluations")
        if cached and cached["signature"] == signature:
            scores = cached["scores"]
            scores["inference_latency"] = metric(records[-1]["inference_ms"] if records else None, "ms", len(records), "ok" if records else "no_data")
            return scores, resolved
        window = {"start": resolved[0]["target_date"], "end": resolved[-1]["target_date"], "horizon_days": 28} if resolved else None
        unit = self.dataset()["rows"][0]["unit"]
        scores = accuracy([r["actual"] for r in resolved], [r["prediction"] for r in resolved], window, unit=unit)
        self.store.append("model_evaluations", dict(signature=signature, version=version, scores=scores, resolved=resolved))
        scores["inference_latency"] = metric(records[-1]["inference_ms"] if records else None, "ms", len(records), "ok" if records else "no_data")
        return scores, resolved

    def collect(self):
        """Periodic acquisition. UI polling only reads the resulting snapshot."""
        now = time.time()
        dataset = self.dataset()
        as_of = self.as_of()
        current = [r for r in dataset["rows"] if r["available_on"] <= as_of]
        scores, resolved = self.model_metrics()
        calibration = self.store.latest("calibration") or self.store.latest("monitor_calibration") or {}
        if calibration.get("unit") != current[0]["unit"]:
            calibration = {}
        # A calibration built on another feature definition (old price-level rule) must not judge returns.
        same_feature = calibration.get("feature") == "weekly_log_return"
        inputs = drift(weekly_returns(current), calibration.get("input") if same_feature else None, unit=RETURN_UNIT)
        relations = {}
        rapid = {}
        if len(current) >= 2:
            a, b = current[-2:]
            rapid["price_change"] = metric((b["midpoint"]/a["midpoint"]-1)*100, "%", 2,
                                           window={"start": a["date"], "end": b["date"]},
                                           reason="마지막 실제 관측 간 변화; 간격을 함께 확인")
        else:
            rapid["price_change"] = metric(None, "%", len(current), "insufficient")
        rule = RULES["service"]
        service = service_summary(self.store.read("requests"), rule["window_seconds"])
        latency, errors = service["latency_mean"]["value"] or 0, service["error_rate"]["value"] or 0
        conditions = [("service", service["request_count"]["value"] >= rule["min_requests"] and
                       (latency > rule["max_mean_latency_ms"] or errors > rule["max_error_rate_pct"]),
                       str(int(now//60)), f"서비스 경계 초과: 5분 평균 지연 {latency:.0f}ms(경계 {rule['max_mean_latency_ms']}), "
                                          f"에러율 {errors:.1f}%(경계 {rule['max_error_rate_pct']})")]
        observation = current[-1]["date"] if current else "none"
        deployment = self.store.latest("deployments")
        epoch = f"{deployment['version']}:{deployment['at']}" if deployment else "no-deployment"
        input_namespace = calibration.get("version", epoch)
        input_checks = {name: evaluate_input(self.store, name, m["value"], m["threshold"], observation, input_namespace,
                                             log=self.log)
                        for name, m in inputs.items()}
        # Retraining trigger is WAPE (scale-free); RMSE stays the model-comparison metric.
        performance = calibration.get("wape", [])
        if len(performance) >= 20 and scores["wape"]["n"] >= 13 and scores["wape"]["value"] is not None:
            threshold = float(np.quantile(performance, .95))
            scores["wape"]["threshold"] = threshold
            # Count a week only if the newest matured forecast also misses: one old big miss alone can keep the
            # 13-week WAPE high for 13 weeks and must not look like a lasting change.
            newest = abs(resolved[-1]["actual"]-resolved[-1]["prediction"])/abs(resolved[-1]["actual"])*100
            scores["wape"]["newest_error"] = newest
            conditions.append((f"performance@{epoch}", scores["wape"]["value"] > threshold and newest > threshold,
                               f"{self.active()}:{resolved[-1]['target_date']}",
                               f"drift detected: 최근 13건 WAPE {scores['wape']['value']:.2f}% > 경계 {threshold:.2f}%"))
        scores["wape"]["alarm_status"] = "calibrated" if len(performance) >= 20 else "uncalibrated"
        for key, exceeded, token, message in conditions:
            count = check_alert(self.store, key, exceeded, token, now, 60 if key == "service" else 0)
            if count is None:
                continue
            kind = "performance" if key.startswith("performance@") else "monitor"
            if key.startswith("performance@"):
                scores["wape"]["streak"] = count
            # 1단계: 2회 연속 초과 → 경보만. 2단계: 성능 초과가 retrain_consecutive회 이어짐 → 파인튜닝.
            # A one-off shock lifts the 13-week WAPE for a few weeks; a changed regime keeps it lifted.
            if count >= RULES["monitoring"]["warn_consecutive"]:
                self.alert(key, kind, message)
                if (key.startswith("performance@") and count >= RULES["monitoring"]["retrain_consecutive"]
                        and not any(r["token"] == token for r in self.store.read("retrain_candidates"))):
                    if self.retrain_handler:
                        self.retrain_handler(current, token)
                    else:
                        self.store.append("retrain_candidates", dict(token=token, version=self.active(), at=stamp(), status="검증용 학습 대기"))
            elif not exceeded:
                previous_alert = {r["key"]: r for r in self.store.read("alerts")}.get(key)
                if previous_alert and previous_alert["status"] == "open":
                    self.alert(key, kind, "경계 내 복귀", status="resolved")
        quality = data.quality(dataset["rows"], as_of=date.fromisoformat(self.as_of()))
        self.alert("freshness", "data_freshness", "예정 발표 이후 새 데이터 없음" if quality["delay_days"] else "발표 일정 범위 내",
                   status="open" if quality["delay_days"] else "resolved")
        resources = dict(process_cpu=metric(self.process.cpu_percent(), "% (1 core=100)", 1),
                         process_rss=metric(self.process.memory_info().rss/1024**2, "MiB", 1),
                         host_memory=metric(psutil.virtual_memory().percent, "%", 1),
                         disk=metric(psutil.disk_usage(str(self.store.root)).percent, "%", 1))
        jobs = [j for j in self.jobs() if j["kind"] not in ("simulation", "experiment")]
        complete = [j for j in jobs if j["status"] in ("succeeded", "failed", "cancelled")]
        job_metrics = dict(success_rate=metric(sum(j["status"] == "succeeded" for j in complete)/len(complete)*100 if complete else None,
                                               "%", len(complete), "ok" if complete else "no_data"),
                           queue_depth=metric(sum(j["status"] == "queued" for j in jobs), "건", len(jobs)),
                           last_success=next((j["ended_at"] for j in complete if j["status"] == "succeeded"), None))
        job_metrics["by_kind"] = {}
        for kind in ("training", "retraining", "experiment"):
            completed = [j for j in complete if j["kind"] == kind]
            times = [j["ended_ts"]-j["started_ts"] for j in completed if "started_ts" in j]
            waits = [j["started_ts"]-j["queued_ts"] for j in jobs if j["kind"] == kind and "started_ts" in j]
            job_metrics["by_kind"][kind] = dict(
                success_rate=metric(sum(j["status"] == "succeeded" for j in completed)/len(completed)*100 if completed else None,
                                    "%", len(completed), "ok" if completed else "no_data"),
                duration=metric(float(np.mean(times)) if times else None, "s", len(times), "ok" if times else "no_data"),
                wait=metric(float(np.mean(waits)) if waits else None, "s", len(waits), "ok" if waits else "no_data"))
        if not self.store.latest("metrics") or self.store.latest("metrics")["data_version"] != dataset["version"]:
            self.event(dataset["version"], "monitor", "succeeded")
        snapshot = dict(at=stamp(), ts=now, service=service, model=scores, drift=inputs, relations=relations, rapid=rapid,
                        resources=resources, jobs=job_metrics, observation=observation, data_version=dataset["version"],
                        input_checks=input_checks, calibration_version=calibration.get("version"))
        self.store.append("metrics", snapshot)
        return snapshot

