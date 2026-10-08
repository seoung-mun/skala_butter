"""Integration: matured WAPE vs calibrated bound → 2 consecutive → alert only (level 1) → N consecutive → fine-tune."""
from datetime import date, timedelta

from buttercast.data import RULES
from buttercast.operations import Operations
from buttercast.store import Store

START = date(2020, 1, 5)
ROWS = [dict(date=(START+timedelta(weeks=i)).isoformat(), available_on=(START+timedelta(weeks=i, days=7)).isoformat(),
             midpoint=100.0, unit="EUR/100kg", interval_days=7, source="fixture", synthetic=False) for i in range(120)]


def test_wape_trigger_boundary_streak_dedupe_and_reset(tmp_path):
    store = Store(tmp_path)
    ops = Operations(store)
    calls = []
    ops.retrain_handler = lambda rows, token: calls.append((token, rows[-1]["date"]))
    store.append("datasets", dict(version="d1", rows=ROWS))
    store.append("deployments", dict(version="v1", at="t0"))
    store.append("calibration", dict(unit="EUR/100kg", input={}, wape=[5.0]*20, version="v1"))  # bound = 5.0%
    week = [20]

    def observe(prediction):
        """One new matured forecast (one new observation), then a monitoring pass."""
        target = ROWS[week[0]]
        store.append("predictions", dict(version="v1", horizon_days=28, issued_at="x", target_date=target["date"],
                                         prediction=prediction, inference_ms=1))
        store.append("replay_clock", dict(as_of=target["available_on"]))
        week[0] += 1
        return ops.collect()["model"]["wape"]

    def alerts():
        return [a for a in store.read("alerts") if a["kind"] == "performance"]

    for _ in range(13):
        wape = observe(100.0)                       # below the bound
    assert wape["value"] == 0 and wape["threshold"] == 5.0 and not alerts()
    for _ in range(13):
        wape = observe(95.0)                        # exactly at the bound: not an exceedance
    assert wape["value"] == 5.0 and not alerts()
    observe(50.0)                                   # 1st exceedance: no alert yet
    assert not alerts() and not calls
    observe(50.0)                                   # 2nd consecutive new observation: alert, no retrain yet
    assert len(alerts()) == 1 and alerts()[0]["status"] == "open" and not calls
    retrain = RULES["monitoring"]["retrain_consecutive"]
    for _ in range(retrain-3):
        observe(50.0)                               # still watching
    assert not calls
    wape = observe(50.0)                            # retrain_consecutive-th: sustained → fine-tune
    assert wape["streak"] == retrain
    assert len(calls) == 1 and calls[0][1] <= store.latest("replay_clock")["as_of"]
    opened = len(alerts())
    ops.collect()                                   # same observation again: no count, no second call
    assert len(calls) == 1 and len(alerts()) == opened
    for _ in range(13):
        observe(100.0)                              # back within the bound: alert resolved
    assert alerts()[-1]["status"] == "resolved"
    log = (tmp_path/"aiops.log").read_text()
    assert log.index("[WARN] performance open") < log.index("[INFO] performance resolved")
    ops.executor.shutdown(wait=True)
