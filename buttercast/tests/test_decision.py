"""Purchase advice: signal thresholds, scoring against what happened, and caller-typed future prices."""
import pytest

from buttercast.data import AUDIT, RULES, load_eu
from buttercast.decision import judge, signal, track_record
from buttercast.experiments import custom_future

RULE = RULES["decision"]


def test_signal_thresholds_come_from_rules():
    buy, hold = RULE["buy_change_pct"], RULE["hold_change_pct"]
    assert signal(100, 100*(1+buy/100))["signal"] == "buy"
    assert signal(100, 100*(1+hold/100))["signal"] == "hold"
    assert signal(100, 100*(1+(buy-.01)/100))["signal"] == "normal"
    assert signal(100, 100*(1+(hold+.01)/100))["signal"] == "normal"
    with pytest.raises(ValueError, match="양수"):
        signal(0, 100)


def test_judge_scores_hit_and_saving_per_100kg():
    bought = judge(400, 420, 430)       # buy signal, price rose: bought 30 cheaper than in 4 weeks
    assert (bought["signal"], bought["hit"], bought["gain"]) == ("buy", True, 30)
    held = judge(400, 380, 410)         # hold signal, price rose: waiting cost 10
    assert (held["signal"], held["hit"], held["gain"]) == ("hold", False, -10)
    usual = judge(400, 401, 450)        # no signal: nothing to score
    assert usual["hit"] is None and usual["gain"] is None


def test_track_record_counts_signals_and_missed_jumps():
    jump = 100*(1+(RULE["missed_jump_pct"]+1)/100)
    record = track_record([dict(current=100, forecast=110, actual=105), dict(current=100, forecast=90, actual=95),
                           dict(current=100, forecast=90, actual=101), dict(current=100, forecast=100, actual=jump)])
    assert record["summary"]["buy"]["hits"] == 1
    assert (record["summary"]["hold"]["count"], record["summary"]["hold"]["hits"]) == (2, 1)
    assert record["signals"] == 3 and record["hit_rate"] == pytest.approx(200/3)
    assert record["mean_gain"] == pytest.approx((5+5-1)/3) and record["missed_jumps"] == 1


def test_custom_future_continues_weekly_after_last_real_week():
    rows = load_eu(AUDIT/"eu-butter-weekly.csv")
    future = custom_future(rows, [450, 455.5])
    assert [r["midpoint"] for r in future] == [450, 455.5] and all(r["synthetic"] for r in future)
    assert future[0]["date"] > rows[-1]["date"] and future[0]["available_on"] > future[0]["date"]
    with pytest.raises(ValueError, match="양수"):
        custom_future(rows, [450, -1])
