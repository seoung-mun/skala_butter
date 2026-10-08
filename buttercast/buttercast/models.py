"""Purged chronological learning; optional dependencies load only when requested."""
import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np

from .data import RULES
from .metrics import accuracy, metric

SEQUENCE = 6
MIN_ROWS = 104
FEATURES = ["log_return_lags"]
REGISTERED_NAME = "ButterCast_Predictor"


def build_samples(rows, sequence=SEQUENCE, interval_days=14):
    rows = sorted(rows, key=lambda r: r["date"])
    by_date = {r["date"]: r for r in rows}
    samples = []
    for index in range(sequence-1, len(rows)):
        current = rows[index]
        context = rows[index-sequence+1:index+1]
        if any((date.fromisoformat(b["date"])-date.fromisoformat(a["date"])).days != interval_days
               for a, b in zip(context, context[1:])):
            continue
        origin = current["available_on"] if interval_days == 7 else current["date"]
        target_date = (date.fromisoformat(origin)+timedelta(days=28)).isoformat()
        target = by_date.get(target_date)
        if target is None or current["available_on"] >= target_date:
            continue
        if any(r["available_on"] > current["available_on"] for r in context):
            continue
        samples.append(dict(x=[[r["midpoint"]] for r in context], actual=target["midpoint"],
                            target_date=target_date, issued_at=current["available_on"],
                            actual_known_at=target["available_on"], origin_date=current["date"]))
    return samples


def prepare(rows, interval_days=14, finetune=False):
    if len(rows) < MIN_ROWS:
        raise ValueError(f"최소 {MIN_ROWS}개 실제 관측 필요: 현재 {len(rows)}개. 합성 데이터로 대체하지 않습니다.")
    samples = build_samples(rows, interval_days=interval_days)
    if len(samples) < 80:
        raise ValueError("연속 구간의 유효 학습 예제가 80개 미만입니다")
    if finetune:
        # Fine-tune on the recent window (it contains the drift) and judge on the newest weeks it never saw.
        rule = RULES["gate"]["finetune"]
        holdout = samples[-rule["holdout_weeks"]:]
        train = [s for s in samples[-(rule["window_weeks"]+rule["holdout_weeks"]):-rule["holdout_weeks"]]
                 if s["actual_known_at"] < holdout[0]["issued_at"]]
        values = [x[0] for s in train for x in s["x"]]+[s["actual"] for s in train]
        return dict(train=train, validation=holdout, test=holdout), dict(min=min(values), max=max(values))
    a, b = int(len(samples)*.6), int(len(samples)*.8)
    validation, test = samples[a:b], samples[b:]
    train = [s for s in samples[:a] if s["actual_known_at"] < validation[0]["issued_at"]]
    validation = [s for s in validation if s["actual_known_at"] < test[0]["issued_at"]]
    values = [x[0] for s in train for x in s["x"]]+[s["actual"] for s in train]
    low, high = min(values), max(values)
    if high == low:
        raise ValueError("학습 가격이 상수여서 정규화할 수 없습니다")
    return dict(train=train, validation=validation, test=test), dict(min=low, max=high)


def log_returns(prices):
    """Scale-free inputs: a level shift outside the training range no longer breaks the model."""
    prices = np.asarray(prices, dtype=np.float64)
    if (prices <= 0).any() or not np.isfinite(prices).all():
        raise ValueError("가격은 유한 양수여야 합니다")
    return np.diff(np.log(prices), axis=-1)


def dependencies():
    try:
        import mlflow
        import tensorflow as tf
    except ImportError as error:
        raise RuntimeError("ML 의존성 필요: .venv/bin/pip install -e '.[ml]' 실행") from error
    return tf, mlflow


def finetune_gate(comparisons):
    """Fine-tune gate on the newest unseen weeks: a sanity cap only. The decisive check, strictly better than the
    serving model on the same weeks, is added by versus_champion(strict=True) once the champion is scored."""
    limit = RULES["gate"]["finetune"]["max_wape_percent"]
    value = comparisons["validation"]["lstm"]["wape"]["value"]
    checks = [dict(name="wape", label="최근 13주 WAPE", value=value, limit=limit, op="<=",
                   passed=value is not None and value <= limit)]
    return dict(metric="finetune_recent", value=value, max_percent=limit, checks=checks,
                passed=checks[0]["passed"], rules_version=RULES["version"])


def gate(comparisons):
    """Deployment gate on validation: WAPE, scale-free RMSE (% of mean price) and 4-week direction accuracy.

    All three must pass. `value`/`max_percent` stay the WAPE pair the logs and screens already show.
    """
    rules, scores = RULES["gate"], comparisons["validation"]["lstm"]
    checks = [dict(name="wape", label="WAPE", value=scores["wape"]["value"], limit=rules["max_wape_percent"], op="<="),
              dict(name="rmse_pct", label="RMSE/평균가", value=scores["rmse_pct"]["value"],
                   limit=rules["max_rmse_percent"], op="<="),
              dict(name="direction", label="방향 정확도", value=scores["direction"]["value"],
                   limit=rules["min_direction_percent"], op=">=")]
    for check in checks:
        value = check["value"]
        check["passed"] = value is not None and (value <= check["limit"] if check["op"] == "<=" else value >= check["limit"])
    return dict(metric="validation", value=checks[0]["value"], max_percent=checks[0]["limit"], checks=checks,
                passed=all(c["passed"] for c in checks), rules_version=RULES["version"])


def versus_champion(decision, champion, champion_wape, strict=False, candidate_wape=None, weeks=None):
    """Add the champion check on evaluation weeks the champion never trained on (same weeks, same metric).
    Full training: not worse (≤ champion × ratio). Fine-tuning (strict): must be better (< champion), otherwise a
    retrain that changed nothing would be promoted. Too few unseen weeks → no fair test: recorded as not applicable."""
    value = decision["value"] if candidate_wape is None else candidate_wape
    op = "<" if strict else "<="
    if champion_wape is None:
        check = dict(name="vs_champion", label=f"운영 {champion} 대비 WAPE", value=None, limit=None, op=op,
                     champion=champion, champion_value=None, weeks=weeks, applicable=False, passed=not strict,
                     note=f"운영 모델이 학습하지 않은 주가 {weeks}주뿐이라 공정한 비교 불가")
    else:
        limit = champion_wape if strict else champion_wape*RULES["gate"]["max_wape_vs_champion"]
        check = dict(name="vs_champion", label=f"운영 {champion} 대비 WAPE", value=value, limit=round(limit, 2),
                     op=op, champion=champion, champion_value=champion_wape, weeks=weeks, applicable=True)
        check["passed"] = value is not None and (value < limit if strict else value <= limit)
    checks = [c for c in decision["checks"] if c["name"] != "vs_champion"]+[check]
    return dict(decision, checks=checks, passed=all(c["passed"] for c in checks))


OPS = {"<=": "≤", ">=": "≥", "<": "<"}


def gate_summary(decision):
    """One log/toast line: `WAPE 4.56%≤6 · RMSE/평균가 6.39%≤8 · 방향 정확도 69.5%≥60`."""
    return " · ".join((f"{c['label']} 비교 불가({c['weeks']}주)" if c.get("applicable") is False else
                       f"{c['label']} {c['value']:.2f}%{OPS[c['op']]}{c['limit']:g}")
                      + ("" if c["passed"] else " ✗") for c in decision["checks"])


def train_bundle(rows, folder: Path, epochs=20, synthetic=False, interval_days=14,
                 on_epoch=None, base=None, patience=5, finetune=False):
    """base: folder of the serving bundle to warm-start from (fine-tuning keeps its scale).
    finetune: recent-window training with a fixed epoch count and no early stopping, since its only held-out
    weeks are the ones the gate judges on (stopping on them would leak them into model selection)."""
    if finetune and not base:
        raise ValueError("파인튜닝은 운영 모델(base)에서만 이어서 학습합니다")
    splits, level_scale = prepare(rows, interval_days=interval_days, finetune=finetune)
    if finetune:
        epochs, patience = RULES["gate"]["finetune"]["epochs"], None
    units = {row.get("unit", "USD/ton") for row in rows}
    if len(units) != 1:
        raise ValueError("학습 가격 단위를 혼합할 수 없습니다")
    unit = units.pop()
    tf, mlflow = dependencies()
    tf.keras.utils.set_random_seed(42)

    def arrays(part):
        raw = np.asarray([[v[0] for v in s["x"]] for s in splits[part]], dtype=np.float64)
        target = np.log(np.asarray([s["actual"] for s in splits[part]])/raw[:, -1])
        return raw, log_returns(raw), target

    _, x, y = arrays("train")
    _, val_x, val_y = arrays("validation")
    if base:
        base_meta = json.loads((Path(base)/"bundle.json").read_text())
        if base_meta.get("feature_schema") != FEATURES:
            raise ValueError("구버전 묶음에서는 이어서 학습할 수 없습니다")
        scale = dict(base_meta["scale"])
        lstm = tf.keras.models.load_model(Path(base)/"lstm.keras", compile=False)
        lstm.compile(optimizer=tf.keras.optimizers.Adam(RULES["gate"]["finetune"]["learning_rate"]), loss="mse")
    else:
        scale = dict(level_scale, x_sd=float(x.std()), y_sd=float(y.std()))
        lstm = tf.keras.Sequential([tf.keras.Input(shape=(SEQUENCE-1, 1)), tf.keras.layers.LSTM(12),
                                    tf.keras.layers.Dense(1)])
        lstm.compile(optimizer="adam", loss="mse")
    if not scale["x_sd"] > 0 or not scale["y_sd"] > 0:
        raise ValueError("학습 수익률 분산이 0이어서 정규화할 수 없습니다")
    x, val_x = (x/scale["x_sd"])[..., None], (val_x/scale["x_sd"])[..., None]
    y, val_y = y/scale["y_sd"], val_y/scale["y_sd"]
    learning_history, best, best_weights, waited = [], np.inf, lstm.get_weights(), 0
    for epoch in range(epochs):
        # train_on_batch avoids hidden random shuffling and a large tf.data threadpool.
        for start in range(0, len(x), 16):
            lstm.train_on_batch(x[start:start+16], y[start:start+16])
        train_mse = float(np.mean((lstm(x, training=False).numpy().ravel()-y)**2))
        val_mse = float(np.mean((lstm(val_x, training=False).numpy().ravel()-val_y)**2))
        if not np.isfinite([train_mse, val_mse]).all():
            raise ValueError(f"epoch {epoch+1}: 비유한 학습 손실")
        improved = val_mse < best
        if improved:
            best, best_weights, waited = val_mse, lstm.get_weights(), 0
        else:
            waited += 1
        record = dict(epoch=epoch+1, train_mse=train_mse, validation_mse=val_mse, best=improved)
        learning_history.append(record)
        if on_epoch:
            on_epoch(record)
        if patience is not None and waited >= patience:
            break
    if not finetune:
        lstm.set_weights(best_weights)  # Restore the epoch with the lowest validation loss.
    best_epoch = len(learning_history) if finetune else min(learning_history, key=lambda r: r["validation_mse"])["epoch"]
    comparisons, predictions = {}, {}
    for partition in ("validation", "test"):
        records = splits[partition]
        raw, returns, _ = arrays(partition)
        features = (returns/scale["x_sd"])[..., None]
        last = raw[:, -1]
        p_lstm = last*np.exp(lstm(features, training=False).numpy().ravel()*scale["y_sd"])
        actual = np.asarray([s["actual"] for s in records])
        scores = accuracy(actual, p_lstm, unit=unit)
        scores["rmse_pct"] = metric(scores["rmse"]["value"]/float(actual.mean())*100, "%", len(actual))
        # Did the forecast call the 4-week move (up/down) right, measured from the last known price?
        scores["direction"] = metric(float(np.mean(np.sign(p_lstm-last) == np.sign(actual-last))*100), "%", len(actual))
        comparisons[partition] = {"lstm": scores}
        predictions[partition] = [dict(s, prediction=float(p_lstm[i])) for i, s in enumerate(records)]
    decision = finetune_gate(comparisons) if finetune else gate(comparisons)
    # The weights that will serve, and the last label they saw (champion comparisons only use later weeks).
    served, served_scale = lstm, scale
    trained_through = splits["train"][-1]["actual_known_at"]
    if not finetune and decision["passed"] and RULES["gate"]["refit"]:
        # Gate passed on held-out weeks → refit the same recipe on every labelled week so the served model has
        # seen the latest regime. The gate scores above stay the out-of-sample evidence; the refit has none of
        # its own, which is what the post-swap check and the champion test on later weeks cover.
        every = build_samples(rows, interval_days=interval_days)
        raw = np.asarray([[v[0] for v in s["x"]] for s in every], dtype=np.float64)
        all_x, all_y = log_returns(raw), np.log(np.asarray([s["actual"] for s in every])/raw[:, -1])
        served_scale = dict(level_scale, x_sd=float(all_x.std()), y_sd=float(all_y.std()))
        tf.keras.utils.set_random_seed(42)
        served = tf.keras.Sequential([tf.keras.Input(shape=(SEQUENCE-1, 1)), tf.keras.layers.LSTM(12),
                                      tf.keras.layers.Dense(1)])
        served.compile(optimizer="adam", loss="mse")
        fx, fy = (all_x/served_scale["x_sd"])[..., None], all_y/served_scale["y_sd"]
        for epoch in range(best_epoch):
            for start in range(0, len(fx), 16):
                served.train_on_batch(fx[start:start+16], fy[start:start+16])
            if on_epoch:
                on_epoch(dict(epoch=epoch+1, phase="refit", train_mse=float(np.mean((served(fx, training=False).numpy().ravel()-fy)**2)),
                              validation_mse=None, best=False))
        trained_through = every[-1]["actual_known_at"]
    reference_x = splits["test"][-1]["x"]
    reference_raw = np.asarray([v[0] for v in reference_x], dtype=np.float64)
    reference = float(reference_raw[-1]*np.exp(served((log_returns(reference_raw)/served_scale["x_sd"]).reshape(1, SEQUENCE-1, 1),
                                                       training=False).numpy().ravel()[0]*served_scale["y_sd"]))
    folder.mkdir(parents=True, exist_ok=False)
    served.save(folder/"lstm.keras")
    metadata = dict(scale=served_scale, sequence=SEQUENCE, refit=served is not lstm, trained_through=trained_through,
                    reference=dict(x=reference_x, prediction=reference), selection_scale=scale, model="lstm", horizon_days=28,
                    feature_schema=FEATURES, target="log(price[t+4w]/price[t])", comparisons=comparisons,
                    gate=decision, validation_passed=decision["passed"], synthetic=synthetic, seed=42,
                    epochs=epochs, epochs_run=len(learning_history), best_epoch=best_epoch,
                    warm_start_from=Path(base).name if base else None,
                    training_mode="finetune_recent" if finetune else "full",
                    unit=unit, interval_days=interval_days, learning_history=learning_history,
                    horizon_anchor="available_on" if interval_days == 7 else "observation_date",
                    partition_counts={part: len(records) for part, records in splits.items()},
                    train_end=splits["train"][-1]["actual_known_at"],
                    validation_end=splits["validation"][-1]["actual_known_at"],
                    test_start=splits["test"][0]["issued_at"],
                    notice="게이트는 학습구간만으로 학습한 선택 모델의 검증 점수; 통과하면 같은 설정으로 전체 기간 재학습한 모델을 서빙")
    (folder/"bundle.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2))
    (folder/"evaluation.json").write_text(json.dumps(predictions, ensure_ascii=False))
    track(folder, metadata)
    return metadata


def track(folder: Path, metadata):
    """Log a bundle folder as an MLflow run in the store that owns it (folder = <store>/models/<version>).

    Also used when an experiment's fine-tuned bundle is adopted into the main store, so that store's
    registry can point `production` at it. Writes the new run id back into bundle.json.
    """
    _, mlflow = dependencies()
    root = folder.parent.parent
    mlflow.set_tracking_uri(f"sqlite:///{root/'mlflow-tracking.db'}")
    if mlflow.get_experiment_by_name("ButterCast") is None:
        mlflow.create_experiment("ButterCast", artifact_location=(root/"mlartifacts").as_uri())
    mlflow.set_experiment("ButterCast")
    with mlflow.start_run(run_name=folder.name) as run:
        mlflow.log_params({"sequence": SEQUENCE, "model": "lstm", "synthetic": metadata["synthetic"], "seed": 42,
                           "target": metadata["target"], "warm_start_from": metadata["warm_start_from"],
                           "unit": metadata["unit"], "interval_days": metadata["interval_days"],
                           "epochs": metadata["epochs"], "from_experiment": metadata.get("from_experiment")})
        for record in metadata["learning_history"]:
            mlflow.log_metrics({"train_mse": record["train_mse"], "validation_mse": record["validation_mse"]},
                               step=record["epoch"])
        mlflow.log_metrics({f"{part}_lstm_{key}": scores[key]["value"]
                            for part, by_model in metadata["comparisons"].items() for scores in by_model.values()
                            for key in ("rmse", "wape", "rmse_pct", "direction") if scores[key]["value"] is not None})
        mlflow.set_tag("gate", "passed" if metadata["gate"]["passed"] else "failed")
        mlflow.log_artifacts(str(folder), artifact_path="bundle")
        metadata["mlflow_run_id"] = run.info.run_id
    (folder/"bundle.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2))
    return run.info.run_id


def register_production(root: Path, run_id, version):
    """Register the promoted bundle in the MLflow Model Registry and move the `production` alias."""
    _, mlflow = dependencies()
    mlflow.set_tracking_uri(f"sqlite:///{Path(root)/'mlflow-tracking.db'}")
    client = mlflow.MlflowClient()
    if not client.search_registered_models(filter_string=f"name='{REGISTERED_NAME}'"):
        client.create_registered_model(REGISTERED_NAME)
    # Rollback re-points the alias to the bundle's existing registry version instead of re-registering.
    existing = client.search_model_versions(f"name='{REGISTERED_NAME}' and run_id='{run_id}'")
    if existing:
        number = existing[0].version
    else:
        source = f"{client.get_run(run_id).info.artifact_uri}/bundle"
        number = client.create_model_version(REGISTERED_NAME, source, run_id=run_id, tags={"bundle": version}).version
    client.set_registered_model_alias(REGISTERED_NAME, "production", number)
    return dict(name=REGISTERED_NAME, version=int(number), alias="production")


class Bundle:
    def __init__(self, folder):
        tf, _ = dependencies()
        self.metadata = json.loads((folder/"bundle.json").read_text())
        if self.metadata.get("feature_schema") != FEATURES:
            raise ValueError(f"구버전 모델 묶음({self.metadata.get('feature_schema')}): 로그수익률 묶음으로 재학습 필요")
        self.lstm = tf.keras.models.load_model(folder/"lstm.keras", compile=False)

    def predict(self, prices):
        if len(prices) < SEQUENCE:
            raise ValueError(f"연속 가격 {SEQUENCE}개 필요")
        scale = self.metadata["scale"]
        raw = np.asarray(prices[-SEQUENCE:], dtype=np.float64)
        features = (log_returns(raw)/scale["x_sd"]).reshape(1, SEQUENCE-1, 1)
        return dict(prediction=float(raw[-1]*np.exp(self.lstm(features, training=False).numpy().ravel()[0]*scale["y_sd"])))

    def predict_many(self, sequences):
        """Vectorised predict for scoring a whole validation split (e.g. the champion on a challenger's weeks)."""
        scale = self.metadata["scale"]
        raw = np.asarray([s[-SEQUENCE:] for s in sequences], dtype=np.float64)
        features = (log_returns(raw)/scale["x_sd"])[..., None]
        return raw[:, -1]*np.exp(self.lstm(features, training=False).numpy().ravel()*scale["y_sd"])
