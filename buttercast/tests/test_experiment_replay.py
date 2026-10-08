import numpy as np
import pytest

from buttercast.data import AUDIT, load_eu
from buttercast.experiments import replay_inputs

ROWS = load_eu(AUDIT / "eu-butter-weekly.csv")


def test_injection_changes_only_isolated_copy():
    original = ROWS[520]["midpoint"]
    stream = replay_inputs(ROWS, start=520, normal=13, changed=26, recovery=13, factor=.2, kind="level_ramp")
    assert len(stream) == 52
    assert all(item["row"]["midpoint"] == item["original_price"] for item in stream[:13])
    assert all(item["row"]["midpoint"] == pytest.approx(item["original_price"]*1.2) for item in stream[17:39])
    assert all(item["row"]["synthetic"] is True for item in stream[13:39])
    assert ROWS[520]["midpoint"] == original
    assert stream[-1]["phase"] == "recovery" and stream[-1]["row"]["synthetic"] is False
    with pytest.raises(ValueError, match="기간"):
        replay_inputs(ROWS, start=1340, normal=13, changed=26, recovery=13, kind="variance")
    with pytest.raises(ValueError, match="주입 종류"):
        replay_inputs(ROWS, start=520, normal=13, changed=26, recovery=13, kind="mean")


def test_level_ramp_reaches_rule_pct_after_ramp_weeks():
    stream = replay_inputs(ROWS, 1000, 13, 26, 13, kind="level_ramp")  # rules.json: +15% over 4 weeks
    ratios = [item["row"]["midpoint"]/item["original_price"] for item in stream[13:39]]
    assert ratios[:4] == pytest.approx([1.0375, 1.075, 1.1125, 1.15])
    assert ratios[4:] == pytest.approx([1.15]*22)


def test_variance_injection_triples_weekly_log_returns_and_stays_positive():
    stream = replay_inputs(ROWS, 1000, 13, 26, 65, kind="variance")  # rules.json: factor 3
    changed = stream[13:39]
    prices = [stream[12]["row"]["midpoint"]]+[item["row"]["midpoint"] for item in changed]
    originals = [stream[12]["original_price"]]+[item["original_price"] for item in changed]
    assert np.diff(np.log(prices)) == pytest.approx(3*np.diff(np.log(originals)))
    assert all(price > 0 for price in prices)
    assert stream[-1]["row"]["midpoint"] == ROWS[1000+103]["midpoint"]


def test_none_kind_is_pure_real_replay():
    stream = replay_inputs(ROWS, 1000, 13, 26, 13, kind="none")
    assert all(item["row"] == ROWS[1000+i] and not item["injected"] for i, item in enumerate(stream))
