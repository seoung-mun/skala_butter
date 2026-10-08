"""Shared real learning, artifact checks, deployment, and retraining trace."""
import hashlib
import json
import shutil
import time
from datetime import date

import numpy as np

from . import data, models
from .data import RULES
from .metrics import accuracy, stamp
from .monitoring import calibrate


class Pipeline:
    def __init__(self, ops, experiment_id=None):
        self.ops, self.store = ops, ops.store
        self.experiment_id = experiment_id
        if experiment_id and self.store.root.name != experiment_id:
            raise ValueError("실험 저장소와 실험 ID 불일치")

    def check_artifacts(self, version, expected=None):
        folder = self.store.root / "models" / version
        names = ["lstm.keras", "evaluation.json", "bundle.json"]
        if not all((folder / name).is_file() for name in names):
            raise ValueError("모델 파일 누락")
        hashes = {name: hashlib.sha256((folder / name).read_bytes()).hexdigest() for name in names}
        if expected and hashes != expected:
            raise ValueError("모델 파일 해시 불일치")
        bundle = models.Bundle(folder)  # Always reload; cached objects must not hide corruption.
        # The served weights' own reference prediction (a refit model differs from the scored selection model).
        first = bundle.metadata.get("reference") or json.loads((folder / "evaluation.json").read_text())["test"][0]
        start = time.perf_counter()
        result = bundle.predict([value[0] for value in first["x"]])
        if not np.isfinite(list(result.values())).all() or not np.isclose(result["prediction"], first["prediction"], rtol=1e-5):
            raise ValueError("모델 재로딩 후 예측 유효성/일치 검사 실패")
        return dict(hashes=hashes, reload_verified=True,
                    first_inference_ms=(time.perf_counter()-start)*1000)

    def train_sync(self, job_id, version, rows, trigger_id=None, epochs=20, max_seconds=300):
        started = time.perf_counter()
        self.ops.event(job_id, "train", "running")
        synthetic = None if any(row["synthetic"] is None for row in rows) else any(row["synthetic"] for row in rows)
        folder = self.store.root / "models" / version
        def record_epoch(record):
            self.store.append("epochs", dict(job_id=job_id, at=stamp(), **record))
            self.ops.check_cancel(job_id)
            if time.perf_counter()-started > max_seconds:
                raise TimeoutError(f"학습 실행시간 {max_seconds}초 초과: epoch 경계에서 중단")
        active = self.ops.active()
        # Alarm-triggered retraining fine-tunes the serving model on recent weeks; manual training starts fresh.
        finetune = bool(trigger_id and active)
        base = self.store.root / "models" / active if finetune else None
        metadata = models.train_bundle(rows, folder, epochs=epochs, interval_days=rows[0]["interval_days"],
                                       synthetic=synthetic, on_epoch=record_epoch, base=base, finetune=finetune)
        if active:
            # Challenger vs champion on the challenger's validation weeks. bundle.json keeps the absolute gate
            # (its hash is already fixed); the store record below carries the full decision.
            # Only weeks the champion never trained on make a fair comparison (a refit champion saw everything
            # up to its own training date).
            evaluation = json.loads((folder / "evaluation.json").read_text())["validation"]
            seen = next(m for m in reversed(self.store.read("models")) if m["version"] == active)
            seen_until = seen.get("trained_through") or seen["train_end"]
            fresh = [row for row in evaluation if row["actual_known_at"] > seen_until]
            if len(fresh) >= RULES["gate"]["champion_min_weeks"]:
                champion = self.ops.bundle(active).predict_many([[v[0] for v in row["x"]] for row in fresh])
                score = lambda values: accuracy([row["actual"] for row in fresh], values, unit=rows[0]["unit"])["wape"]["value"]  # noqa: E731
                metadata["gate"] = models.versus_champion(metadata["gate"], active, score(champion), strict=finetune,
                                                          candidate_wape=score([row["prediction"] for row in fresh]),
                                                          weeks=len(fresh))
            else:
                metadata["gate"] = models.versus_champion(metadata["gate"], active, None, strict=finetune, weeks=len(fresh))
            metadata["validation_passed"] = metadata["gate"]["passed"]
        decision = metadata["gate"]
        self.ops.log("GATE PASSED" if decision["passed"] else "GATE FAILED",
                     f"{version} {models.gate_summary(decision)} (warm_start={metadata['warm_start_from']})")
        self.ops.check_cancel(job_id)
        self.ops.event(job_id, "train", "succeeded")
        self.ops.event(job_id, "artifact_check", "running")
        health = self.check_artifacts(version)
        self.ops.event(job_id, "artifact_check", "succeeded")
        calibration = calibrate(rows, metadata["test_start"])
        evaluation = json.loads((folder / "evaluation.json").read_text())["validation"]
        rmse, wape = [], []
        for end in range(13, len(evaluation)+1):
            window = evaluation[end-13:end]
            scores = accuracy([row["actual"] for row in window], [row["prediction"] for row in window],
                              unit=rows[0]["unit"])
            rmse.append(scores["rmse"]["value"])
            wape.append(scores["wape"]["value"])
        if finetune:
            # 13 held-out weeks give one window, too few for a WAPE bound: keep the base model's bound.
            inherited = next(c for c in reversed(self.store.read("bundle_calibrations")) if c["version"] == active)
            rmse, wape = inherited["rmse"], inherited["wape"]
        self.store.append("bundle_calibrations", dict(calibration, version=version, rmse=rmse, wape=wape))
        record = dict(metadata, version=version, data_version=data.version(rows), trigger_id=trigger_id,
                      source=rows[0]["source"],
                      experiment_id=self.experiment_id, at=stamp(), artifact_health=health,
                      operational_validation_passed=True)
        self.store.append("models", record)
        if self.ops.active() is None:
            self.store.append("monitor_calibration", dict(calibration, version=version, rmse=rmse, wape=wape))
        self.ops.event(job_id, "validation", "succeeded" if metadata["validation_passed"] else "rejected")
        # Gate passed → promote automatically (first training and retraining alike); no manual approval.
        promoted = False
        new = f"new_wape={decision['value']:.2f}%"
        if metadata["validation_passed"]:
            promoted = not self.deploy(version, "auto_promote").get("rolled_back")
            if promoted:
                self.ops.log("OK", f"{new} production promoted {version} (previous {active})")
        else:
            self.ops.log("FAIL", f"{new} gate failed; keep serving {active}")
        return dict(version=version, validation_passed=metadata["validation_passed"], artifact_health=health,
                    trigger_id=trigger_id, promoted=promoted, gate=decision)

    def submit_train(self, rows, epochs=20, trigger_id=None, max_seconds=300):
        models.prepare(rows, interval_days=rows[0]["interval_days"])
        # Capture this observation snapshot before queuing; future arrivals cannot change it.
        rows = [dict(row) for row in rows]
        with self.store.lock:
            if trigger_id:
                previous = next((row for row in self.store.read("retrain_candidates") if row["token"] == trigger_id), None)
                if previous:
                    return next(job for job in self.ops.jobs() if job["id"] == previous["job_id"])
                recent = self.store.latest("retrain_candidates")
                if recent and recent.get("observation_date"):
                    elapsed = (date.fromisoformat(rows[-1]["date"])-date.fromisoformat(recent["observation_date"])).days
                    if elapsed < 91:
                        self.store.append("retrain_decisions", dict(token=trigger_id, status="cooldown", at=stamp(),
                                                                  observation_date=rows[-1]["date"], minimum_days=91))
                        self.ops.log("INFO", f"retrain skipped: cooldown 91일 미경과 ({trigger_id})")
                        return dict(status="cooldown", id=recent["job_id"])
            # Sequential names (v1, v2, …) reserved under the lock; a failed job leaves a gap like MLflow does.
            version = self.next_version()
            job = self.ops.submit("retraining" if trigger_id else "training",
                                  lambda job_id: self.train_sync(job_id, version, rows, trigger_id, epochs, max_seconds),
                                  spec=dict(type="train"))  # a worker rebuilds it from training_inputs
            self.store.append("training_inputs", dict(job_id=job["id"], version=version, rows=rows, epochs=epochs,
                                                      trigger_id=trigger_id, data_version=data.version(rows), at=stamp(),
                                                      max_seconds=max_seconds))
            if trigger_id:
                self.store.append("retrain_candidates", dict(token=trigger_id, job_id=job["id"], at=stamp(),
                                                            data_version=data.version(rows), status="queued",
                                                            observation_date=rows[-1]["date"]))
                self.ops.log("INFO", f"retrain triggered job={job['id']} rows={len(rows)} until={rows[-1]['date']} "
                                     f"({trigger_id})")
            return job

    def next_version(self):
        """Next free `vN` across training reservations and model folders (call under the lock)."""
        names = [row.get("version") or "" for row in self.store.read("training_inputs")]
        names += [path.name for path in (self.store.root / "models").glob("v*")]
        numbers = [int(name[1:]) for name in names if name[:1] == "v" and name[1:].isdigit()]
        return f"v{max(numbers, default=0)+1}"

    def adopt_experiment(self, experiment_store, result):
        """Register every model the experiment trained in this store as a new version (rejected ones too, so the
        registry history shows the whole run) and serve the newest one that passed its gate.
        They are labelled `from_experiment` because made-up or injected weeks were in their training data.
        """
        adopted, passed = [], []
        for retrain in result["retraining"]:
            source = next(m for m in experiment_store.read("models") if m["version"] == retrain["version"])
            calibration = next(c for c in experiment_store.read("bundle_calibrations") if c["version"] == retrain["version"])
            with self.store.lock:
                version = self.next_version()
                folder = self.store.root / "models" / version
                shutil.copytree(experiment_store.root / "models" / retrain["version"], folder)
            metadata = json.loads((folder / "bundle.json").read_text())
            metadata.update(from_experiment=result["id"], experiment_kind=result["kind"],
                            experiment_version=retrain["version"])
            run_id = models.track(folder, metadata)
            health = self.check_artifacts(version)
            self.store.append("models", dict(source, version=version, at=stamp(), mlflow_run_id=run_id,
                                             artifact_health=health, from_experiment=result["id"],
                                             experiment_kind=result["kind"], experiment_version=retrain["version"],
                                             trigger_id=retrain["job_id"]))
            self.store.append("bundle_calibrations", dict(calibration, version=version))
            adopted.append(version)
            if retrain["promoted"]:
                passed.append(version)
        self.ops.log("INFO", f"experiment {result['id']} ({result['kind']}) registered {adopted or 'no new model'}")
        if passed and not self.deploy(passed[-1], "experiment_promote").get("rolled_back"):
            self.ops.log("OK", f"experiment {result['id']} production promoted {passed[-1]}")
        return adopted

    def retry(self, job_id, max_seconds=None):
        job = next((row for row in self.ops.jobs() if row["id"] == job_id), None)
        if job is None or job["status"] != "failed":
            raise ValueError("종료된 실패 학습 작업만 재시도 가능")
        snapshot = next((row for row in self.store.read("training_inputs") if row["job_id"] == job_id), None)
        if snapshot is None:
            raise ValueError("원래 입력 스냅샷 없음: 현재 데이터로 임의 재시도하지 않습니다")
        # Manual retry uses exactly the failed input; new market arrivals cannot alter it.
        retried = self.submit_train(snapshot["rows"], epochs=snapshot["epochs"],
                                    max_seconds=max_seconds if max_seconds is not None else snapshot.get("max_seconds", 300))
        self.store.append("retries", dict(original_job_id=job_id, retry_job_id=retried["id"],
                                         data_version=snapshot["data_version"], at=stamp()))
        return retried

    def deploy(self, version, action="auto_promote", acknowledge_degraded=False):
        candidate = next((row for row in self.store.read("models") if row["version"] == version), None)
        if candidate is None:
            raise KeyError("등록된 모델 묶음 없음")
        if candidate.get("synthetic") is None:
            raise ValueError("출처/실제 데이터 여부 미확인")
        if candidate.get("synthetic") and not self.experiment_id and not candidate.get("from_experiment"):
            raise ValueError("변형 데이터 모델은 실험에서 가져온 모델(from_experiment)만 운영 허용")
        dataset = self.ops.dataset()
        if dataset:
            row = dataset["rows"][0]
            if candidate.get("unit") != row["unit"] or candidate.get("interval_days") != row["interval_days"]:
                raise ValueError("데이터/모델 단위·주기 계약 불일치")
            if candidate.get("source") and candidate["source"] != row["source"]:
                raise ValueError("데이터/모델 시장 출처 계약 불일치")
        if not candidate["validation_passed"] and not (self.experiment_id and acknowledge_degraded):
            raise ValueError("검증 점수 미달: 운영 승격 차단")
        if action == "rollback" and not any(row["version"] == version for row in self.store.read("deployments")):
            raise ValueError("이전에 배포된 모델로만 롤백 가능")
        health = self.check_artifacts(version, candidate.get("artifact_health", {}).get("hashes"))
        if not candidate.get("operational_validation_passed"):
            raise ValueError("운영 파일 검증 기록 없음")
        registry = models.register_production(self.store.root, candidate["mlflow_run_id"], version)
        with self.store.lock:
            previous = self.ops.active()
            self.store.append("deployments", dict(version=version, previous=previous, action=action, at=stamp(),
                                                  registry=registry,
                                                  experiment_id=self.experiment_id, quality_passed=candidate["validation_passed"],
                                                  acknowledge_degraded=acknowledge_degraded,
                                                  policy="isolated_operational_v2" if self.experiment_id else "strict_quality_v1"))
            calibration = next(row for row in self.store.read("bundle_calibrations") if row["version"] == version)
            self.store.append("calibration", calibration)
            self.ops.cache.pop(version, None)
        self.ops.event(version, action, "succeeded")
        self.ops.log("INFO", f"{action} {version} (previous {previous}, MLflow {registry['name']} "
                             f"v{registry['version']} @production)")
        result = dict(active=version, previous=previous, action=action, health=health, registry=registry)
        if previous and previous != version and action not in ("rollback", "auto_rollback"):
            problems = self.verify_after_swap(version, previous)
            if problems:
                self.ops.log("ROLLBACK", f"{version} failed post-swap check: {'; '.join(problems)} → back to {previous}")
                back = self.deploy(previous, "auto_rollback", acknowledge_degraded=bool(self.experiment_id))
                return dict(back, rolled_back=problems, rejected=version)
        return result

    def verify_after_swap(self, version, previous):
        """Canary on the real serving path right after the pointer moved: the new model must answer the latest
        weeks with a finite, positive price, within the latency limit, and not jump far from the old model."""
        limits = RULES["post_deploy"]
        dataset, as_of = self.ops.dataset(), self.ops.as_of()
        if dataset is None:
            raise ValueError("교체 후 확인에 쓸 데이터셋이 없습니다: 데이터를 먼저 적재하세요")
        prices = [r["midpoint"] for r in dataset["rows"] if r["available_on"] <= as_of][-models.SEQUENCE:]
        bundle = self.ops.bundle(version)  # load first; the check times serving, not loading
        start = time.perf_counter()
        new = bundle.predict(prices)["prediction"]
        latency = (time.perf_counter()-start)*1000
        old = self.ops.bundle(previous).predict(prices)["prediction"]
        change = abs(new/old-1)*100
        problems = []
        if not np.isfinite(new) or new <= 0:
            problems.append(f"예측값 비정상 {new}")
        if latency > limits["max_latency_ms"]:
            problems.append(f"추론 {latency:.0f}ms > {limits['max_latency_ms']}ms")
        if change > limits["max_change_pct"]:
            problems.append(f"직전 모델 대비 예측 {change:.1f}% 변화 > {limits['max_change_pct']}%")
        self.store.append("post_deploy_checks", dict(version=version, previous=previous, new=new, old=old,
                                                     change_pct=change, latency_ms=latency, problems=problems,
                                                     at=stamp()))
        return problems
