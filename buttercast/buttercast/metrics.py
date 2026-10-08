import time
from datetime import datetime, timezone

import numpy as np
from scipy.stats import ks_2samp, wasserstein_distance


def stamp():
    return datetime.now(timezone.utc).isoformat()


def metric(value, unit, n, status="ok", reason=None, window=None, threshold=None):
    return dict(value=value, unit=unit, n=n, status=status, reason=reason,
                window=window, threshold=threshold, updated_at=stamp())


def correlation(a, b):
    if len(a) < 3 or np.ptp(a) == 0 or np.ptp(b) == 0:
        return None
    return float(np.corrcoef(a, b)[0, 1])


def accuracy(actual, predicted, window=None, unit="USD/ton"):
    a, p = np.asarray(actual, dtype=float), np.asarray(predicted, dtype=float)
    if a.shape != p.shape or not np.isfinite(a).all() or not np.isfinite(p).all():
        raise ValueError("Accuracy requires equal, finite arrays")
    n = len(a)
    if not n:
        return {key: metric(None, unit, 0, "no_data", "정답이 확정된 예측 없음", window)
                for key, unit in [("rmse", unit), ("wape", "%"), ("bias", unit), ("pearson", "r")]}
    residual = a - p
    r = correlation(a, p)
    denominator = float(np.abs(a).sum())
    return dict(rmse=metric(float(np.sqrt(np.mean(residual**2))), unit, n, window=window),
                wape=metric(float(np.abs(residual).sum()/denominator*100) if denominator else None,
                            "%", n, "ok" if denominator else "undefined", None if denominator else "분모 0", window),
                bias=metric(float(residual.mean()), unit, n, window=window),
                pearson=metric(r, "r", n, "ok" if r is not None else "undefined",
                               None if r is not None else "상수열 또는 3개 미만 표본", window))


def matured(rows, as_of, version, horizon):
    eligible = [r for r in rows if r["version"] == version and r["horizon_days"] == horizon
                and r.get("actual") is not None and r.get("actual_known_at")
                and r["actual_known_at"][:10] <= as_of[:10]
                and r["target_date"][:10] <= as_of[:10]]
    # One observation per target date, not one per repeated button click.
    unique = {r["target_date"]: r for r in sorted(eligible, key=lambda x: x["issued_at"])}
    return sorted(unique.values(), key=lambda x: x["target_date"])[-13:]


def drift(values, calibration=None, unit="USD/ton"):
    a = np.asarray(values, dtype=float)
    if not np.isfinite(a).all():
        raise ValueError("Drift requires finite real observations")
    if len(a) < 65:
        return {k: metric(None, u, len(a), "insufficient", "기준 52 + 최근 13개 실제 관측 필요")
                for k, u in [("ks", "D"), ("wasserstein", unit)]}
    reference, current = a[-65:-13], a[-13:]
    vals = dict(ks=float(ks_2samp(reference, current).statistic),
                wasserstein=float(wasserstein_distance(reference, current)))
    result = {}
    for key, value in vals.items():
        history = (calibration or {}).get(key, [])
        bound = float(np.quantile(history, .95)) if len(history) >= 20 else None
        result[key] = metric(value, "D" if key == "ks" else unit, 13,
                             "uncalibrated" if bound is None else "exceeded" if value > bound else "ok",
                             "보정 윈도 20개 필요" if bound is None else None,
                             {"reference_n": 52, "current_n": 13}, bound)
        if key == "ks" and bound is not None and bound >= 1:
            result[key].update(status="saturated", reason="KS 경계가 최댓값 1.0: 이 규칙의 초과 경보 불가능")
    return result


def relationship_drift(rows, calibration=None):
    result = {}
    for name, transform in [("price_width_level", lambda a: a), ("price_width_change", np.diff)]:
        value, status, reason = None, "insufficient", "기준 52 + 최근 13개 실제 관측 필요"
        if len(rows) >= 65:
            ref, recent = rows[-65:-13], rows[-13:]
            a = correlation(transform([r["midpoint"] for r in ref]), transform([r["high"]-r["low"] for r in ref]))
            b = correlation(transform([r["midpoint"] for r in recent]), transform([r["high"]-r["low"] for r in recent]))
            value = abs(b-a) if a is not None and b is not None else None
            status, reason = "uncalibrated", "보정 윈도 20개 필요"
            if value is None:
                status, reason = "undefined", "수준 또는 변화율에 상수열 포함"
        history = (calibration or {}).get(name, [])
        bound = float(np.quantile(history, .95)) if len(history) >= 20 else None
        if value is not None and bound is not None:
            status, reason = "exceeded" if value > bound else "ok", None
        result[name] = metric(value, "Δr", min(len(rows), 13), status, reason,
                              {"reference_n": 52, "current_n": 13, "features": ["midpoint", "range_width"]}, bound)
    return result


def service_summary(rows, seconds=300, now=None):
    now = time.time() if now is None else now
    recent = [r for r in rows if now-seconds <= r["ts"] <= now and r["category"] == "business"]
    n = len(recent)
    window = {"start": now-seconds, "end": now, "seconds": seconds}
    def m(v, unit, count=n):
        return metric(v, unit, count, "ok" if count else "no_data", window=window)
    result = {"request_count": m(n, "건"), "rps": m(n/seconds, "req/s"),
              "success_rate": m(sum(200 <= r["status"] < 300 for r in recent)/n*100 if n else None, "%"),
              "error_rate": m(sum(r["status"] >= 500 for r in recent)/n*100 if n else None, "%"),
              "client_error_rate": m(sum(400 <= r["status"] < 500 for r in recent)/n*100 if n else None, "%")}
    for prefix, subset in [("latency", recent),
                           ("success_latency", [r for r in recent if 200 <= r["status"] < 300]),
                           ("failed_latency", [r for r in recent if r["status"] >= 400])]:
        values = [r["latency_ms"] for r in subset]
        for suffix, function in [("mean", np.mean), ("p50", lambda a: np.quantile(a, .5)),
                                 ("p95", lambda a: np.quantile(a, .95))]:
            result[f"{prefix}_{suffix}"] = m(float(function(values)) if values else None, "ms", len(values))
    result["request_count"]["status"] = "ok"
    return result
