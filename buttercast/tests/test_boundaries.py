"""User scenarios on every operating boundary: exactly at a limit passes, one step past it does not.

Values come from rules.json so the tests follow the team's numbers if they change.
"""
from datetime import date, timedelta

import pytest

from buttercast.data import RULES, check_units
from buttercast.models import gate
from buttercast.operations import Operations
from buttercast.store import Store

GATE = RULES["gate"]
EPS = 0.01


def scores(wape=4.0, rmse_pct=5.0, direction=70.0):
    lstm = {"wape": {"value": wape}, "rmse_pct": {"value": rmse_pct}, "direction": {"value": direction}}
    return {"validation": {"lstm": lstm}}


def failed(decision):
    return [check["name"] for check in decision["checks"] if not check["passed"]]


@pytest.mark.parametrize("name, at_limit, past_limit", [
    ("wape", GATE["max_wape_percent"], GATE["max_wape_percent"]+EPS),
    ("rmse_pct", GATE["max_rmse_percent"], GATE["max_rmse_percent"]+EPS),
    ("direction", GATE["min_direction_percent"], GATE["min_direction_percent"]-EPS),
])
def test_operator_gate_each_metric_at_and_past_its_limit(name, at_limit, past_limit):
    """운영자: 지표가 기준값과 같으면 배포, 기준을 조금이라도 넘으면 그 지표 하나 때문에 차단."""
    assert gate(scores(**{name: at_limit}))["passed"] is True
    blocked = gate(scores(**{name: past_limit}))
    assert blocked["passed"] is False and failed(blocked) == [name]


def test_operator_gate_missing_metric_blocks():
    """정답이 없어 지표를 못 구하면(None) 통과로 치지 않는다."""
    assert failed(gate(scores(direction=None))) == ["direction"]


START = date(2020, 1, 5)
ROWS = [dict(date=(START+timedelta(weeks=i)).isoformat(), available_on=(START+timedelta(weeks=i, days=7)).isoformat(),
             midpoint=100.0, unit="EUR/100kg", interval_days=7, source="fixture", synthetic=False) for i in range(80)]


def monitored(tmp_path):
    """Serving model with a 5% WAPE bound; each observe() matures one forecast and runs one monitoring pass."""
    store, calls = Store(tmp_path), []
    ops = Operations(store)
    ops.retrain_handler = lambda rows, token: calls.append(token)
    store.append("datasets", dict(version="d1", rows=ROWS))
    store.append("deployments", dict(version="v1", at="t0"))
    store.append("calibration", dict(unit="EUR/100kg", input={}, wape=[5.0]*20, version="v1"))
    week = [20]

    def observe(prediction):
        target = ROWS[week[0]]
        store.append("predictions", dict(version="v1", horizon_days=28, issued_at="x", target_date=target["date"],
                                         prediction=prediction, inference_ms=1))
        store.append("replay_clock", dict(as_of=target["available_on"]))
        week[0] += 1
        return ops.collect()["model"]["wape"]

    for _ in range(13):
        assert observe(95.0)["value"] == 5.0  # window sits exactly on the bound: not an exceedance
    alerts = lambda: [a for a in store.read("alerts") if a["kind"] == "performance" and a["status"] == "open"]  # noqa: E731
    return ops, observe, calls, alerts


def test_operator_short_shock_stops_at_watch_level(tmp_path):
    """일회성 충격: 경계 초과가 retrain_consecutive-1회에서 끝나면 경보(1단계)만, 파인튜닝 없음."""
    ops, observe, calls, alerts = monitored(tmp_path)
    warn, retrain = RULES["monitoring"]["warn_consecutive"], RULES["monitoring"]["retrain_consecutive"]
    observe(60.0)
    assert not alerts() and not calls                     # warn-1 exceedances: still level 0
    for _ in range(warn-1):
        observe(60.0)
    assert alerts() and not calls                         # warn exceedances: level 1
    for _ in range(retrain-1-warn):
        observe(60.0)
    assert not calls                                      # retrain-1 exceedances: still only watching
    wape = observe(100.0)                                 # new forecast is right again …
    assert wape["value"] > wape["threshold"] and wape["streak"] == 0  # … old misses alone don't count
    for _ in range(12):
        wape = observe(100.0)                             # shock leaves the 13-week window
    assert wape["value"] <= wape["threshold"] and not calls
    ops.executor.shutdown(wait=True)


def test_operator_sustained_degradation_fine_tunes_exactly_once(tmp_path):
    """지속 악화: retrain_consecutive번째 연속 초과에서 정확히 1번 파인튜닝."""
    ops, observe, calls, _ = monitored(tmp_path)
    retrain = RULES["monitoring"]["retrain_consecutive"]
    for _ in range(retrain-1):
        observe(60.0)
    assert not calls
    assert observe(60.0)["streak"] == retrain and len(calls) == 1
    ops.collect()                                         # same observation re-read: no second job
    assert len(calls) == 1
    ops.executor.shutdown(wait=True)


def test_data_engineer_weekly_ratio_limits_are_inclusive():
    """적재: 주간 가격 비율이 정확히 경계(0.5배·2배)면 받고, 넘으면 단위 혼입으로 거절."""
    low, high = RULES["data_quality"]["min_weekly_ratio"], RULES["data_quality"]["max_weekly_ratio"]
    week = lambda i, price: dict(date=f"2026-01-{i:02d}", midpoint=price)  # noqa: E731
    check_units([week(1, 100.0), week(8, 100.0*high), week(15, 100.0*high*low)])
    with pytest.raises(ValueError, match="단위 혼입"):
        check_units([week(1, 100.0), week(8, 100.0*high*1.001)])
    with pytest.raises(ValueError, match="단위 혼입"):
        check_units([week(1, 100.0), week(8, 100.0*low*0.999)])


def test_operator_champion_comparison_needs_unseen_weeks():
    """운영 모델이 학습한 적 없는 주가 부족하면 비교하지 않는다(처음 학습은 통과, 파인튜닝은 증명 못 하니 탈락)."""
    from buttercast.models import versus_champion
    base = gate(scores())
    full, tune = versus_champion(base, "v1", None, weeks=3), versus_champion(base, "v1", None, strict=True, weeks=3)
    assert full["passed"] and not tune["passed"]
    assert {c["name"]: c["applicable"] for c in full["checks"] if c["name"] == "vs_champion"} == {"vs_champion": False}
    assert not versus_champion(base, "v1", 4.0, strict=True, candidate_wape=4.0, weeks=13)["passed"]  # tie fails
    assert versus_champion(base, "v1", 4.0, strict=True, candidate_wape=3.99, weeks=13)["passed"]
