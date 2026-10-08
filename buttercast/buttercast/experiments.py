"""Isolated historical replay: real prices, labelled drift injection, actual model jobs."""
import json
import math
import shutil
import time
from datetime import date, timedelta

import numpy as np

from . import business, models
from .data import RULES, version
from .metrics import accuracy, stamp
from .operations import Operations
from .pipeline import Pipeline
from .store import Store

KINDS = ("none", "level_ramp", "variance")
# The three operating situations each scenario is built to show (0 일반 / 1 이상 입력·감시만 / 2 파인튜닝).
EXPECTED_LEVEL = {"none": 0, "level_ramp": 1, "variance": 2}


def year_starts(rows, length):
    """{year: index of its first week} for years whose replay (`length` weeks) fits after 104 training weeks."""
    starts = {}
    for index, row in enumerate(rows):
        starts.setdefault(int(row["date"][:4]), index)
    return {year: index for year, index in starts.items() if index >= 104 and index+length <= len(rows)}


def virtual_future(rows, weeks):
    """Weeks after the last real observation, for testing the model that serves today: weekly log-returns resampled
    from the recent history (seeded, so a scenario always plays out the same way). Marked synthetic."""
    rule = RULES["virtual_future"]
    recent = np.diff(np.log([r["midpoint"] for r in rows[-(rule["history_weeks"]+1):]]))
    rng = np.random.default_rng(rule["seed"])
    last = rows[-1]
    lag = date.fromisoformat(last["available_on"])-date.fromisoformat(last["date"])
    price, day, future = last["midpoint"], date.fromisoformat(last["date"]), []
    for _ in range(weeks):
        price, day = price*math.exp(float(rng.choice(recent))), day+timedelta(days=7)
        future.append(dict(last, date=day.isoformat(), available_on=(day+lag).isoformat(), midpoint=price, synthetic=True))
    return future


def injection_params(kind, factor=None):
    params = dict(RULES["drift_injection"][kind])
    if factor is not None and kind == "variance":
        params["factor"] = factor
    if factor is not None and kind == "level_ramp":
        params["pct"] = factor
    return params


def replay_inputs(rows, start, normal, changed, recovery, factor=None, kind="level_ramp"):
    """Copy rows[start:...] and inject `kind` only into the `changed` phase (rules.json defaults)."""
    if kind not in KINDS:
        raise ValueError(f"알 수 없는 주입 종류: {kind}")
    if start < 104 or start+normal+changed+recovery > len(rows):
        raise ValueError("재현 기간이 데이터 범위를 벗어납니다")
    params = injection_params(kind, factor)
    result, previous_source, previous_price = [], rows[start+normal-1]["midpoint"], None
    for index, source in enumerate(rows[start:start+normal+changed+recovery]):
        row = dict(source)
        phase = "normal" if index < normal else "changed" if index < normal+changed else "recovery"
        if phase == "changed" and kind != "none":
            k = index-normal
            if kind == "level_ramp":
                price = source["midpoint"]*(1+params["pct"]*min(1, (k+1)/params["ramp_weeks"]))
            else:  # variance: every weekly log-return is multiplied by `factor`
                base = previous_price if previous_price is not None else previous_source
                price = base*(source["midpoint"]/previous_source)**params["factor"]
            row.update(midpoint=price, synthetic=True)
            previous_price = price
        if phase == "changed":
            previous_source = source["midpoint"]
        result.append(dict(row=row, phase=phase, original_price=source["midpoint"],
                           injected=phase == "changed" and kind != "none"))
    return result


def run_experiment(parent_ops, experiment_id, rows, epochs=20, start=None, normal=13, changed=26, recovery=26,
                   factor=None, kind="level_ramp"):
    """start=None (the app): the model serving now meets a virtual future after the last real week.
    start=<row index> (buttercast.sensitivity): historical replay with a model trained up to that week.
    Either way the price stream lives in its own store so made-up prices never enter the real dataset;
    every model it trains is registered in the main store at the end (see Pipeline.adopt_experiment)."""
    live = start is None
    base = parent_ops.active() if live else None
    if live and base is None:
        raise ValueError("운영 모델이 없습니다: 먼저 학습해 운영 모델을 만든 뒤 실험하세요")
    if live:
        start, rows = len(rows), rows+virtual_future(rows, normal+changed+recovery)
    stream = replay_inputs(rows, start, normal, changed, recovery, factor, kind)
    store = Store(parent_ops.store.root / "experiments" / experiment_id)
    ops = Operations(store)
    pipeline = Pipeline(ops, experiment_id=experiment_id)
    # Same policy as production: input drift only warns; a sustained WAPE exceedance fine-tunes.
    ops.retrain_handler = lambda current, token: pipeline.submit_train(current, epochs=epochs, trigger_id=token)
    visible = [dict(row) for row in rows[:start]]

    def dataset():
        store.append("datasets", dict(version=version(visible), rows=[dict(row) for row in visible],
                                      source="isolated_EU_replay", experiment_id=experiment_id, imported_at=stamp()))
        store.append("replay_clock", dict(as_of=visible[-1]["available_on"], at=stamp()))

    def finish(job_id):
        # Wait for this exact job; the worker records its terminal state before returning.
        while True:
            current = next(row for row in ops.jobs() if row["id"] == job_id)
            if current["status"] in ("succeeded", "failed", "cancelled"):
                if current["status"] != "succeeded":
                    raise RuntimeError(current["error"])
                return current
            time.sleep(.02)

    def settle():
        for job in ops.jobs():
            if job["status"] in ("queued", "running"):
                finish(job["id"])

    try:
        ops.log("INFO", f"experiment {experiment_id} kind={kind} start={rows[start]['date']} base={base or 'trained here'} "
                        f"normal={normal} changed={changed} recovery={recovery} params={injection_params(kind, factor)}")
        dataset()
        if live:
            initial = base
            # The serving model, its calibration and a run in this store's MLflow (so rollback can re-register it).
            record = next(m for m in reversed(parent_ops.store.read("models")) if m["version"] == base)
            folder = store.root / "models" / base
            shutil.copytree(parent_ops.store.root / "models" / base, folder)
            run_id = models.track(folder, json.loads((folder / "bundle.json").read_text()))
            store.append("models", dict(record, mlflow_run_id=run_id, artifact_health=pipeline.check_artifacts(base)))
            store.append("bundle_calibrations", next(c for c in reversed(parent_ops.store.read("bundle_calibrations"))
                                                     if c["version"] == base))
            pipeline.deploy(base, "experiment_base")
        else:
            initial = finish(pipeline.submit_train(visible, epochs=epochs)["id"])["result"]["version"]
            # A passing baseline is already auto-promoted; a failing one must still serve so the replay can start.
            if ops.active() != initial:
                pipeline.deploy(initial, "baseline_degraded", acknowledge_degraded=True)
        fixed = ops.bundle(initial)
        records, triggers, detection_index = [], [], None
        for index, item in enumerate(stream):
            visible.append(item["row"])
            dataset()
            opened = len(store.read("alerts"))
            snapshot = ops.collect()
            # Repeated collection must not increase observation streaks or duplicate alerts.
            repeated = ops.collect()
            if ({key: row["count"] for key, row in snapshot["input_checks"].items()}
                    != {key: row["count"] for key, row in repeated["input_checks"].items()}):
                raise ValueError("같은 관측 조회로 경보 횟수 증가")
            alarm = any(row["alert"] for row in snapshot["input_checks"].values())
            performance_alarm = any(row["kind"] == "performance" and row["status"] == "open"
                                    for row in store.read("alerts")[opened:])
            streak = snapshot["model"]["wape"].get("streak") or 0
            settle()
            new_jobs = [j for j in ops.jobs() if j["kind"] == "retraining" and j["id"] not in {t["job_id"] for t in triggers}]
            level = 2 if new_jobs else 1 if alarm or streak >= RULES["monitoring"]["warn_consecutive"] else 0
            if level and item["phase"] != "normal" and detection_index is None:
                detection_index = index-normal
            for job in new_jobs:
                triggers.append(dict(job_id=job["id"], date=item["row"]["date"], phase=item["phase"],
                                     promoted=job["result"]["promoted"], version=job["result"]["version"],
                                     gate=job["result"]["gate"]))
            prices = [row["midpoint"] for row in visible[-6:]]
            baseline = fixed.predict(prices)
            inference_start = time.perf_counter()
            adaptive = ops.bundle(ops.active()).predict(prices)
            inference_ms = (time.perf_counter()-inference_start)*1000
            target = (date.fromisoformat(item["row"]["available_on"])+timedelta(days=28)).isoformat()
            store.append("predictions", dict(version=ops.active(), issued_at=item["row"]["available_on"],
                                             target_date=target, horizon_days=28, inference_ms=inference_ms, **adaptive))
            record = dict(index=index, date=item["row"]["date"], phase=item["phase"], injected=item["injected"],
                          price=item["row"]["midpoint"], original_price=item["original_price"],
                          drift=snapshot["drift"], checks=snapshot["input_checks"], alarm=alarm,
                          performance_alarm=performance_alarm, performance_streak=streak, level=level,
                          wape=snapshot["model"]["wape"], target_date=target, fixed=baseline["prediction"],
                          adaptive=adaptive["prediction"], active_model=ops.active(), at=stamp())
            store.append("replay_steps", record)
            records.append(record)
        before_rollback = ops.active()
        rollback = pipeline.deploy(initial, action="rollback", acknowledge_degraded=True)
        if rollback["active"] != initial:
            raise ValueError("롤백 버전 불일치")
        folder = store.root / "models" / initial
        original = (folder / "lstm.keras").read_bytes()
        try:
            (folder / "lstm.keras").write_bytes(b"intentional isolated corruption")
            try:
                pipeline.deploy(initial, acknowledge_degraded=True)
            except ValueError:
                corruption_blocked = ops.active() == initial
            else:
                raise ValueError("손상 모델 배포 허용")
        finally:
            (folder / "lstm.keras").write_bytes(original)
        observations = {row["date"]: row for row in visible}
        as_of = ops.as_of()
        scored = [record for record in records if record["target_date"] in observations
                  and observations[record["target_date"]]["available_on"] <= as_of]
        scores = {name: accuracy([observations[record["target_date"]]["midpoint"] for record in scored],
                                 [record[name] for record in scored], unit=visible[0]["unit"])
                  for name in ("fixed", "adaptive")}
        dates, prices = [r["date"] for r in records], [r["price"] for r in records]
        normal_steps = [record for record in records if record["phase"] == "normal"]
        tokens = [row["token"] for row in store.read("retrain_candidates")]
        result = dict(id=experiment_id, kind=kind, params=injection_params(kind, factor), isolated=True,
                      source="EU actual prices" + ("" if kind == "none" else " + labelled injection"),
                      policy="production: input drift → WARN only; WAPE streak ≥ retrain_consecutive → fine-tune → gate",
                      expected_level=EXPECTED_LEVEL[kind],
                      observed_level=max(record["level"] for record in records if record["phase"] != "normal"),
                      level_weeks={phase: [sum(r["level"] == lv for r in records if r["phase"] == phase) for lv in (0, 1, 2)]
                                   for phase in ("normal", "changed", "recovery")},
                      initial_model=initial, initial_gate=next(m for m in store.read("models") if m["version"] == initial)["gate"],
                      replaced_model=before_rollback, rolled_back_model=ops.active(),
                      normal_observations=len(normal_steps),
                      normal_alarm_observations=sum(record["level"] > 0 for record in normal_steps),
                      detection_delay_observations=detection_index, injected_observations=changed if kind != "none" else 0,
                      retraining_job_id=triggers[0]["job_id"] if triggers else None, retraining=triggers,
                      duplicate_observation_verified=True, duplicate_training_verified=bool(triggers) and len(tokens) == len(set(tokens)),
                      corruption_blocked=corruption_blocked, scores=scores, evaluated_predictions=len(scored),
                      business=business.compare(dates, prices, [r["fixed"] for r in records], [r["adaptive"] for r in records]),
                      sensitivity=business.sensitivity(dates, prices, [r["adaptive"] for r in records]),
                      final_input_alarm=records[-1]["alarm"], recovery_observations=recovery,
                      jobs=ops.jobs(), deployments=store.read("deployments"), alerts=store.read("alerts"),
                      aiops_log=(store.root / "aiops.log").read_text(encoding="utf-8").splitlines(),
                      steps=records, at=stamp(),
                      notice="격리 실험: 실제 운영 장애·기업 성과 아님. 주입 구간은 synthetic=true로 표시.")
        store.append("experiment_result", result)
        (store.root / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
        # The final fine-tuned model leaves the sandbox: adopted as the next main version and served.
        result["adopted_models"] = Pipeline(parent_ops).adopt_experiment(store, result)
        parent_ops.store.append("experiments", result)
        parent_ops.log("INFO", f"experiment {experiment_id} ({kind}) finished: retraining={len(triggers)} "
                               f"promoted={sum(t['promoted'] for t in triggers)}")
        return dict(experiment_id=experiment_id, retraining_job_id=result["retraining_job_id"],
                    adopted_models=result["adopted_models"],
                    detection_delay_observations=detection_index)
    finally:
        ops.executor.shutdown(wait=True)
