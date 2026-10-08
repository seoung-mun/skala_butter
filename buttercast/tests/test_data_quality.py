"""Data-quality faults must fail ingestion with a cause — they are not market drift."""
import pytest
from fastapi.testclient import TestClient

from buttercast.app import create_app

HEADER = "week_ending,butter_price_usd_lb\n"
GOOD = ["2026-08-29,2.50", "2026-09-05,2.55", "2026-09-12,2.60", "2026-09-19,2.58"]


@pytest.mark.parametrize("lines, cause", [
    (GOOD[:2]+GOOD[3:], "누락"),                                   # missing week
    (GOOD+[GOOD[-1]], "중복"),                                     # duplicate week
    (GOOD[:3]+["2026-09-19,-2.58"], "유효하지 않은 가격"),           # negative price
    (GOOD[:3]+["2026-09-19,258.0"], "단위 혼입"),                   # EUR/100kg-scale value in a USD/lb file
])
def test_bad_upload_fails_keeps_data_and_records_cause(tmp_path, lines, cause):
    with TestClient(create_app(tmp_path)) as client:
        before = client.get("/api/datasets").json()["version"]
        response = client.post("/api/datasets/import", files={"file": ("x.csv", (HEADER+"\n".join(lines)).encode())})
        assert response.status_code == 422
        assert cause in response.json()["detail"]
        assert client.get("/api/datasets").json()["version"] == before
        alert = client.get("/api/alerts").json()[0]
        assert alert["kind"] == "data_quality" and cause in alert["message"]
        assert any("[ERROR] data_quality open" in line for line in client.get("/api/logs").json())


def test_stale_source_opens_freshness_alert(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        ops = client.app.state.ops
        last = ops.dataset()["rows"][-1]["available_on"]
        ops.store.append("replay_clock", dict(as_of="2026-12-31"))  # weeks after the next expected release
        ops.collect()
        freshness = [a for a in client.get("/api/alerts").json() if a["key"] == "freshness"][0]
        assert freshness["status"] == "open"
        assert client.get("/api/datasets").json()["quality"]["freshness"] in ("stale", "current")
        assert last < "2026-12-31"
