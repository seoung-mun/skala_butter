"""Real HTTP/model/filesystem verification; never fabricates job/model scores."""
import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from .store import Store


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8093)
    parser.add_argument("--root", type=Path, help="검증 저장소 (기본: runtime/verification/<id>)")
    args = parser.parse_args()
    root = args.root or Path(__file__).resolve().parents[1] / "runtime" / "verification" / uuid.uuid4().hex[:12]
    root.mkdir(parents=True)
    store = Store(root)
    log = (root / "server.log").open("w")
    server = None
    evidence = {"root": str(root), "checks": {}, "jobs": [], "experiments": []}
    base = f"http://127.0.0.1:{args.port}/api"

    def request(path, body=None, raw=None, headers=None):
        payload = json.dumps(body).encode() if body is not None else raw
        options = headers or ({"Content-Type": "application/json"} if body is not None else {})
        req = urllib.request.Request(base+path, data=payload, headers=options)
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, json.load(error)

    def start():
        nonlocal server
        server = subprocess.Popen([sys.executable, "-m", "buttercast.audit_server", "--root", str(root),
                                   "--port", str(args.port)], stdout=log, stderr=log)
        deadline = time.monotonic()+20
        while time.monotonic() < deadline:
            if server.poll() is not None:
                raise RuntimeError("검증 서버 시작 실패: server.log 확인")
            try:
                status, body = request("/health")
            except urllib.error.URLError:
                time.sleep(.05)
                continue
            if status == 200 and body["status"] == "running":
                return
            raise RuntimeError(f"검증 서버 상태 오류: {body}")
        raise TimeoutError("검증 서버 시작 20초 초과")

    def wait_job(job_id, expected="succeeded"):
        deadline = time.monotonic()+600
        while time.monotonic() < deadline:
            status, job = request(f"/jobs/{job_id}")
            assert status == 200
            if job["status"] in ("succeeded", "failed", "cancelled"):
                evidence["jobs"].append(job)
                assert job["status"] == expected, job
                print(json.dumps({"job": job_id, "status": job["status"]}), flush=True)
                return job
            time.sleep(.05)
        raise TimeoutError(f"실제 작업 {job_id} 종료 600초 초과")

    def submit(path, body):
        status, result = request(path, body)
        assert status == 202, result
        return result["id"]

    def check(name, value):
        evidence["checks"][name] = value
        assert value, name
        print(json.dumps({"check": name, "passed": True}), flush=True)

    try:
        start()
        print(json.dumps({"verification_store": str(root), "port": args.port}), flush=True)
        _, ds = request("/datasets")
        check("weekly_real_price_source", len(ds["rows"]) == 1343 and ds["rows"][0]["unit"] == "EUR/100kg")
        before_requests = request("/metrics")[1]["service"]["request_count"]["value"]
        for _ in range(5):
            request("/metrics")
        check("dashboard_poll_excluded", request("/metrics")[1]["service"]["request_count"]["value"] == before_requests)
        timed_out = wait_job(submit("/train", {"epochs": 20, "max_seconds": .1}), expected="failed")
        check("actual_training_timeout", "초과" in timed_out["error"])
        recovered = wait_job(submit(f"/jobs/{timed_out['id']}/retry", {"max_seconds": 300}))
        check("retry_same_saved_input", store.latest("retries")["data_version"] == store.latest("training_inputs")["data_version"])
        candidate = recovered["result"]["version"]
        check("gate_passed_on_real_eu_prices", recovered["result"]["gate"]["passed"])
        check("sequential_version_name", candidate == "v2")  # v1 was the timed-out attempt
        _, listed = request("/models")
        check("gate_pass_auto_promotes", recovered["result"]["promoted"] and listed["active"] == candidate
              and listed["deployments"][-1]["registry"]["alias"] == "production")
        status, forecast = request("/predict", {})
        check("predict_serves_promoted_version", status == 200 and forecast["version"] == candidate)
        check("predict_rejects_invalid_sequence", request("/predict", {"prices": [500, 0, 1, 2, 3, 4]})[0] == 422)
        _, health = request("/health")
        check("health_reports_loaded_model", health["model_loaded"] and health["active_model"] == candidate)
        for kind in ("none", "level_ramp", "variance"):
            experiment_job = wait_job(submit("/experiments", {"kind": kind}))
            _, result = request(f"/experiments/{experiment_job['id']}")
            evidence["experiments"].append({key: result[key] for key in (
                "id", "kind", "params", "normal_observations", "normal_alarm_observations",
                "detection_delay_observations", "retraining", "corruption_blocked", "initial_model",
                "replaced_model", "rolled_back_model", "scores", "business")})
            check(f"{kind}_level_{result['expected_level']}", result["observed_level"] == result["expected_level"])
            check(f"{kind}_fine_tunes_only_at_level_2", bool(result["retraining"]) == (result["expected_level"] == 2))
            check(f"{kind}_rollback", result["rolled_back_model"] == result["initial_model"])
            check(f"{kind}_corruption_blocked", result["corruption_blocked"])
            check(f"{kind}_below_threshold_no_alert", all(not entry["alert"] for step in result["steps"]
                  for entry in step["checks"].values() if entry["value"] <= entry["threshold"]))
            if result["retraining"]:
                trail = "\n".join(result["aiops_log"])
                triggered = trail.index("[INFO] retrain triggered")
                check(f"{kind}_log_order", trail.index("[WARN]") < triggered < trail.index("[GATE ", triggered))
        collision_id = submit("/train", {"epochs": 100})
        collision = root / "models" / store.latest("training_inputs")["version"]
        collision.mkdir(parents=True)
        collision_job = wait_job(collision_id, expected="failed")
        check("actual_model_save_failure", "File exists" in collision_job["error"])
        collision.rmdir()  # Remove only the empty directory created above for this isolated fault.
        wait_job(submit(f"/jobs/{collision_id}/retry", {}))
        check("model_save_failure_recovered", True)
        cancelled_id = submit("/train", {"epochs": 100})
        status, _ = request(f"/jobs/{cancelled_id}/cancel", {})
        check("cancel_accepted", status == 202)
        wait_job(cancelled_id, expected="cancelled")
        interrupted_id = submit("/train", {"epochs": 100})
        deadline = time.monotonic()+10
        while request(f"/jobs/{interrupted_id}")[1]["status"] != "running":
            if time.monotonic() >= deadline:
                raise TimeoutError("중단 검증 작업이 실행 상태에 도달하지 않음")
            time.sleep(.01)
        server.kill()  # Only this verifier's child server, never another project process.
        server.wait(timeout=10)
        start()
        interrupted = wait_job(interrupted_id, expected="failed")
        check("real_process_restart_recovery", "재시작" in interrupted["error"])
        stages = [row for row in store.read("stages") if row["job_id"] == interrupted_id]
        latest = {row["stage"]: row for row in stages}
        check("no_stage_left_running", all(row["status"] != "running" for row in latest.values()))
        wait_job(submit(f"/jobs/{interrupted_id}/retry", {}))
        for _ in range(20):
            request("/predict", {})  # Genuine HTTP business requests, even when approval blocks them.
        time.sleep(5.2)
        _, metrics = request("/metrics")
        check("real_http_service_metrics", metrics["service"]["request_count"]["value"] >= 20
              and metrics["service"]["latency_p95"]["value"] is not None)
        evidence["service"] = metrics["service"]
        evidence["job_metrics"] = metrics["jobs"]
        evidence["aiops_log"] = request("/logs")[1]
        evidence["passed"] = True
    finally:
        (root / "verification.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
        if server and server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=20)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=10)
        log.close()
        print(json.dumps({"verification_report": str(root / "verification.json"),
                          "passed": evidence.get("passed", False)}), flush=True)


if __name__ == "__main__":
    main()
