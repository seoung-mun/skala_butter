"""Synthetic procurement rules on top of replayed prices. Assumptions, not a verified company ROI."""
from datetime import date

import numpy as np

from .data import RULES


def weekly_usage(dates, rules=None):
    """Butter usage in 100kg units: annual tonnes / 52 × season × noise (seeded)."""
    rules = rules or RULES["business"]
    rng = np.random.default_rng(rules["seed"])
    base = rules["annual_usage_tonnes"]*10/52
    season = [rules["season"].get(str(date.fromisoformat(d).month), 1.0) for d in dates]
    noise = rng.normal(1, rules["usage_noise"], len(dates)).clip(.5, 1.5)
    return base*np.asarray(season)*noise


HORIZON_WEEKS = 4  # The model forecasts the price 4 weeks after issue.


def procurement_cost(prices, forecasts, usage, rules=None):
    """Spot-buy each week's usage; when the forecast 4-week rise ≥ trigger, lock today's price
    for `prebuy_share` of the forecast period's usage (weeks t+4 … t+4+prebuy_weeks).

    forecasts=None means the plain spot policy. Reserved stock is paid now and carries
    financing + storage cost on its book value until the week it is used.
    """
    rules = rules or RULES["business"]
    prices, usage = np.asarray(prices, dtype=float), np.asarray(usage, dtype=float)
    if prices.shape != usage.shape or (forecasts is not None and len(forecasts) != len(prices)):
        raise ValueError("가격·사용량·예측 길이가 같아야 합니다")
    holding_rate = (rules["annual_financing"]+rules["annual_storage"])/52
    reserved, basis = np.zeros(len(prices)), np.zeros(len(prices))
    spend = holding = 0.0
    prebuys = 0
    for t, (price, need) in enumerate(zip(prices, usage)):
        weeks = slice(t+HORIZON_WEEKS, min(len(prices), t+HORIZON_WEEKS+rules["prebuy_weeks"]))
        if (forecasts is not None and not reserved[t:].any() and weeks.start < weeks.stop
                and forecasts[t]/price-1 >= rules["prebuy_trigger_pct"]/100):
            reserved[weeks] = rules["prebuy_share"]*usage[weeks]
            basis[weeks] = price
            spend += float(reserved[weeks].sum())*price
            prebuys += 1
        spend += (need-reserved[t])*price
        reserved[t] = 0
        holding += float((reserved*basis).sum())*holding_rate
    return dict(total_cost=spend+holding, prebuys=prebuys, holding_cost=holding,
                units="price unit × 100kg")


def compare(dates, prices, fixed, adaptive, rules=None):
    usage = weekly_usage(dates, rules)
    spot = procurement_cost(prices, None, usage, rules)
    result = dict(spot=spot, fixed_model=procurement_cost(prices, fixed, usage, rules),
                  adaptive_model=procurement_cost(prices, adaptive, usage, rules),
                  usage_100kg=float(usage.sum()), rules_version=RULES["version"],
                  notice="합성 구매 규칙(rules.json) 기반 비교. 실제 기업 절감액이 아님.")
    for name in ("fixed_model", "adaptive_model"):
        result[name]["saving_vs_spot_pct"] = (1-result[name]["total_cost"]/spot["total_cost"])*100
    return result


def sensitivity(dates, prices, forecasts):
    """Same forecasts under alternative assumptions: shows how much the conclusion depends on them."""
    rows = []
    for trigger in (5.0, 8.0, 10.0):
        for share in (.25, .5, .75):
            rules = dict(RULES["business"], prebuy_trigger_pct=trigger, prebuy_share=share)
            usage = weekly_usage(dates, rules)
            spot = procurement_cost(prices, None, usage, rules)["total_cost"]
            model = procurement_cost(prices, forecasts, usage, rules)
            rows.append(dict(trigger_pct=trigger, share=share, prebuys=model["prebuys"],
                             saving_vs_spot_pct=(1-model["total_cost"]/spot)*100))
    return rows


if __name__ == "__main__":
    # Self-check: a correct rise forecast before a jump must beat spot; no signal equals spot.
    p = [100.0]*4+[130.0]*8
    d = [f"2025-03-{i+1:02d}" for i in range(12)]
    flat = compare(d, p, [100.0]*12, [100.0]*12)
    assert flat["adaptive_model"]["total_cost"] == flat["spot"]["total_cost"]
    rise = compare(d, p, [100.0]*12, [120.0]+[100.0]*11)
    assert rise["adaptive_model"]["total_cost"] < rise["spot"]["total_cost"]
    print("business self-check passed")
