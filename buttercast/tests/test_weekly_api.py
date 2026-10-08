from datetime import date

from fastapi.testclient import TestClient

from buttercast.app import create_app
from buttercast.data import AUDIT, load_eu
from buttercast.metrics import drift
from buttercast.models import build_samples


def test_default_api_uses_weekly_price_only_and_correct_units(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        ds = client.get("/api/datasets").json()
        assert ds["quality"]["rows"] == 1343
        assert not ds["quality"]["gaps"]
        assert "fx" not in ds
        stats = client.get("/api/metrics").json()
        assert stats["model"]["rmse"]["unit"] == "EUR/100kg"
        assert stats["drift"]["wasserstein"]["unit"] == "log return"
        assert "fx_change" not in stats["rapid"]
        assert not stats["relations"]


def test_weekly_target_is_four_weeks_after_assumed_issue_date():
    rows = load_eu(AUDIT / "eu-butter-weekly.csv")
    first = build_samples(rows, interval_days=7)[0]
    assert (date.fromisoformat(first["target_date"])-date.fromisoformat(first["issued_at"])).days == 28


def test_weekly_import_validates_quality_and_keeps_dataset_separate(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        raw = (AUDIT / "butter_weekly_train.csv").read_bytes()
        response = client.post("/api/datasets/import", files={"file": ("prices.csv", raw, "text/csv")})
        assert response.status_code == 200
        ds = client.get("/api/datasets").json()
        assert len(ds["rows"]) == 761
        assert ds["rows"][0]["unit"] == "USD/lb"
        assert ds["rows"][0]["synthetic"] is None
        assert "butter_sales_lb" not in ds["schema"]


def test_distribution_unit_is_not_hardcoded():
    value = drift(list(range(65)), unit="EUR/100kg")
    assert value["wasserstein"]["unit"] == "EUR/100kg"
