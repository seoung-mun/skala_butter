"""Purchase timing advice from the 4-week forecast: buy now, buy as usual, or hold.

A signal fires only when the forecast move is large (rules.json `decision`), so it speaks when the model is
confident. Its track record is scored on held-out weeks: for each past forecast, did the price actually move the
way the signal said, and what would following it have saved per 100 kg versus buying 4 weeks later / now.
"""
import numpy as np

from .data import RULES

LABEL = {"buy": "지금 구매", "normal": "평소대로", "hold": "구매 보류"}


def signal(current, forecast, rules=None):
    rules = rules or RULES["decision"]
    if not current > 0 or not forecast > 0:
        raise ValueError(f"가격은 양수여야 합니다: 현재 {current}, 예측 {forecast}")
    change = (forecast/current-1)*100
    kind = "buy" if change >= rules["buy_change_pct"] else "hold" if change <= rules["hold_change_pct"] else "normal"
    return dict(signal=kind, label=LABEL[kind], change_pct=change)


def judge(current, forecast, actual, rules=None):
    """One past forecast scored against what happened. gain = EUR/100kg saved by following the signal:
    buy → bought now instead of 4 weeks later; hold → bought 4 weeks later instead of now."""
    decided = signal(current, forecast, rules)
    actual_change = (actual/current-1)*100
    kind = decided["signal"]
    hit = None if kind == "normal" else actual > current if kind == "buy" else actual < current
    gain = actual-current if kind == "buy" else current-actual if kind == "hold" else None
    return dict(decided, actual_change_pct=actual_change, hit=hit, gain=gain)


def track_record(records, rules=None):
    """records: dicts with current, forecast, actual (+ any labels to carry through)."""
    rules = rules or RULES["decision"]
    judged = [dict(r, **judge(r["current"], r["forecast"], r["actual"], rules)) for r in records]
    summary = {}
    for kind in LABEL:
        rows = [r for r in judged if r["signal"] == kind]
        hits = [r["hit"] for r in rows if r["hit"] is not None]
        gains = [r["gain"] for r in rows if r["gain"] is not None]
        summary[kind] = dict(label=LABEL[kind], count=len(rows), hits=sum(hits),
                             hit_rate=sum(hits)/len(hits)*100 if hits else None,
                             mean_actual_change_pct=float(np.mean([r["actual_change_pct"] for r in rows])) if rows else None,
                             mean_gain=float(np.mean(gains)) if gains else None)
    acted = [r for r in judged if r["hit"] is not None]
    missed = [r for r in judged if r["signal"] == "normal" and r["actual_change_pct"] >= rules["missed_jump_pct"]]
    return dict(summary=summary, signals=len(acted),
                hit_rate=sum(r["hit"] for r in acted)/len(acted)*100 if acted else None,
                mean_gain=float(np.mean([r["gain"] for r in acted])) if acted else None,
                missed_jumps=len(missed), weeks=len(judged), judged=judged)


if __name__ == "__main__":
    rules = dict(buy_change_pct=3.0, hold_change_pct=-3.0, missed_jump_pct=5.0)
    assert signal(100, 103, rules)["signal"] == "buy" and signal(100, 97, rules)["signal"] == "hold"
    assert signal(100, 102.9, rules)["signal"] == "normal"
    record = track_record([dict(current=100, forecast=105, actual=110), dict(current=100, forecast=95, actual=104),
                           dict(current=100, forecast=101, actual=107)], rules)
    assert record["summary"]["buy"]["hits"] == 1 and record["summary"]["hold"]["hits"] == 0
    assert record["mean_gain"] == (10-4)/2 and record["missed_jumps"] == 1 and record["hit_rate"] == 50
    print("decision self-check passed")
