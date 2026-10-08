import pytest

from buttercast import korea
from buttercast.data import RULES


def test_korea_estimate_carries_latest_korean_cost_by_eu_change_to_the_beta():
    """구매팀: EU 가격이 원화로 그대로면 한국 추정도 그대로, 20% 오르면 1.2^β만큼만 오른다."""
    base, rate = korea._latest()
    eurkrw, last = float(rate["usd_per_eur"])*float(rate["krw_per_usd"]), float(base["kr_krw"])
    flat = korea.estimate(float(base["eu_krw"])/eurkrw*100)
    assert flat["krw_per_kg"] == pytest.approx(last) and flat["korea_last_month"] == base["month"][:7]
    up = korea.estimate(float(base["eu_krw"])*1.2/eurkrw*100)
    assert up["krw_per_kg"] == pytest.approx(last*1.2**RULES["korea"]["passthrough_beta"])
    for bad in (0, -1, float("nan")):
        with pytest.raises(ValueError, match="EU 가격"):
            korea.estimate(bad)
