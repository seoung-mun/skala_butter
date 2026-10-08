from buttercast.data import AUDIT
from buttercast.metrics import accuracy
from buttercast.models import build_samples, prepare


def test_real_eu_weekly_data_and_temporal_split():
    from buttercast.data import load_eu

    rows = load_eu(AUDIT / "eu-butter-weekly.csv")
    assert len(rows) == 1343
    assert rows[0]["unit"] == "EUR/100kg"
    assert all(not row["synthetic"] for row in rows)
    assert all(row["publication_verified"] is False for row in rows)
    samples = build_samples(rows, interval_days=7)
    assert len(samples) > 1300
    splits, scale = prepare(rows, interval_days=7)
    assert splits["train"][-1]["actual_known_at"] < splits["validation"][0]["issued_at"]
    assert splits["validation"][-1]["actual_known_at"] < splits["test"][0]["issued_at"]
    altered = [dict(row) for row in rows]
    altered[-1]["midpoint"] = 100000
    assert prepare(altered, interval_days=7)[1] == scale


def test_price_accuracy_preserves_eu_units():
    scores = accuracy([100, 110], [100, 100], unit="EUR/100kg")
    assert scores["rmse"]["unit"] == "EUR/100kg"
    assert scores["bias"]["unit"] == "EUR/100kg"
    assert scores["wape"]["unit"] == "%"
