from datetime import date, timedelta

import pytest


def test_import_validates_unit_and_keeps_original_availability():
    from buttercast.data import parse_usda

    header = "report_date,published_date,price_min,price_max,price_Unit,lot_Desc,region\n"
    body = "01/13/2025,01/16/2025 09:15:01,5000,5500,Dollars per Metric Ton,82% Butterfat,Oceania\n"
    rows = parse_usda(header+body)
    assert rows[0]["midpoint"] == 5250
    assert rows[0]["available_on"] == "2025-01-18"
    assert rows[0]["published_raw"] == "01/16/2025 09:15:01"
    with pytest.raises(ValueError, match="unit|단위"):
        parse_usda((header+body).replace("Dollars per Metric Ton", "USD/kg"))
    with pytest.raises(ValueError, match="중복"):
        parse_usda(header+body+body)


def test_split_purges_future_labels_and_scaler_never_sees_test():
    from buttercast.models import prepare

    start = date(2010, 1, 1)
    rows = [dict(date=(start+timedelta(days=i*14)).isoformat(),
                 available_on=(start+timedelta(days=i*14+5)).isoformat(), midpoint=100+i)
            for i in range(140)]
    splits, scale = prepare(rows)
    assert max(r["actual_known_at"] for r in splits["train"]) < splits["validation"][0]["issued_at"]
    assert scale["max"] < 235
    rows[-1]["midpoint"] = 1000000
    assert prepare(rows)[1] == scale
    with pytest.raises(ValueError, match="104"):
        prepare(rows[:26])
