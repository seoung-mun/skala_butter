"""EU butter forecast → estimated Korean import unit cost (KRW/kg).

Korea's customs unit value follows the EU price 3–4 months late and only partly (origin mix, FX, premium
products). Backtest on 2014–2025 monthly data (docs/korea-price-analysis.md): carrying the latest Korean unit value
forward by the EU change in KRW raised to `passthrough_beta` beat both "latest Korean value as is" and a direct
level conversion. β was fitted on 2014–19 and checked on 2020–25 (MAPE 6.9% vs 7.8%).

    korea_krw_per_kg = korea_last × (eu_eur_forecast × eurkrw_now / eu_krw_at_korea_last_month) ** β
"""
import csv
import math
from pathlib import Path

from .data import AUDIT, RULES


def _rows(path):
    with open(path, newline="", encoding="utf-8") as file:
        return list(csv.DictReader(file))


def _latest(folder=None):
    """(last month with both Korean and EU-in-KRW values, latest daily FX row)."""
    folder = Path(folder or AUDIT)
    monthly = [r for r in _rows(folder/"korea-vs-eu-monthly.csv") if r["kr_krw"] and r["eu_krw"]]
    fx = _rows(folder/"fx-daily.csv")
    if not monthly or not fx:
        raise ValueError("한국 수입단가·환율 데이터가 없습니다: data/korea-vs-eu-monthly.csv, data/fx-daily.csv 확인")
    return monthly[-1], fx[-1]


def estimate(eu_eur_per_100kg, folder=None):
    """Korean import cost estimate for an EU price (EUR/100kg, e.g. the 4-week forecast)."""
    if not math.isfinite(eu_eur_per_100kg) or eu_eur_per_100kg <= 0:
        raise ValueError(f"EU 가격이 유효하지 않습니다: {eu_eur_per_100kg}")
    base, rate = _latest(folder)
    eurkrw = float(rate["usd_per_eur"])*float(rate["krw_per_usd"])
    eu_krw = eu_eur_per_100kg/100*eurkrw
    beta = RULES["korea"]["passthrough_beta"]
    korea_last, change = float(base["kr_krw"]), eu_krw/float(base["eu_krw"])
    return dict(krw_per_kg=korea_last*change**beta, eu_krw_per_kg=eu_krw, eurkrw=eurkrw,
                fx_date=rate["date"], korea_last_month=base["month"][:7],
                korea_last_krw_per_kg=korea_last, eu_change_pct=(change-1)*100, passthrough_beta=beta,
                lag_months=RULES["korea"]["lag_months"],
                notice=f"한국 통관 평균 수입단가 추정(CIF, 관세·유통마진 제외). EU 변화의 {beta:.0%}가 "
                       f"약 {RULES['korea']['lag_months']}개월 늦게 반영된다고 본 값")


if __name__ == "__main__":
    base, rate = _latest()
    eurkrw, last = float(rate["usd_per_eur"])*float(rate["krw_per_usd"]), float(base["kr_krw"])
    flat = estimate(float(base["eu_krw"])/eurkrw*100)       # EU unchanged in KRW → Korean value unchanged
    assert abs(flat["krw_per_kg"]-last) < 1e-6, flat
    up = estimate(float(base["eu_krw"])*1.2/eurkrw*100)     # EU +20% → Korea +20%^β
    assert abs(up["krw_per_kg"]/last-1.2**RULES["korea"]["passthrough_beta"]) < 1e-9
    print("korea self-check passed", round(up["krw_per_kg"]), "KRW/kg at EU +20%")
