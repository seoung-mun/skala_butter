"""Measure Lazy vs Eager: server start → ready, first and second /api/predict latency (real processes)."""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path


def call(url, body=None):
    request = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json"})
    start = time.perf_counter()
    with urllib.request.urlopen(request, timeout=120) as response:
        payload = json.load(response)
    return payload, (time.perf_counter()-start)*1000


def prepare(root):
    """Train once in-process (gate pass auto-promotes) so both modes serve the same bundle."""
    from fastapi.testclient import TestClient

    from .app import create_app
    with TestClient(create_app(root)) as client:
        job = client.post("/api/train", json={"epochs": 40}).json()
        while (job := client.get(f"/api/jobs/{job['id']}").json())["status"] not in ("succeeded", "failed"):
            time.sleep(.2)
        if job["status"] != "succeeded" or not job["result"]["promoted"]:
            raise RuntimeError(job["error"] or f"게이트 탈락: {job['result']['gate']}")


def measure(root, mode, port):
    start = time.perf_counter()
    server = subprocess.Popen([sys.executable, "-m", "uvicorn", "buttercast.app:create_app", "--factory",
                               "--host", "127.0.0.1", "--port", str(port), "--no-access-log"],
                              env=dict(os.environ, LOADING_MODE=mode, BUTTERCAST_RUNTIME=str(root)),
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}/api"
    try:
        while True:
            if server.poll() is not None:
                raise RuntimeError(f"{mode} 서버 시작 실패")
            try:
                health, _ = call(f"{base}/health")
                break
            except OSError:
                time.sleep(.02)
        ready = time.perf_counter()-start
        _, first = call(f"{base}/predict", {})
        _, second = call(f"{base}/predict", {})
        return dict(mode=mode, ready_seconds=round(ready, 3), loaded_at_ready=health["model_loaded"],
                    first_predict_ms=round(first, 1), second_predict_ms=round(second, 1))
    finally:
        server.terminate()
        server.wait(timeout=30)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8097)
    parser.add_argument("--repeat", type=int, default=3)
    args = parser.parse_args()
    root = Path(tempfile.mkdtemp(prefix="buttercast-loading-"))
    prepare(root)
    rows = [measure(root, mode, args.port) for _ in range(args.repeat) for mode in ("lazy", "eager")]
    for row in rows:
        print(json.dumps(row, ensure_ascii=False))
    (root/"loading.json").write_text(json.dumps(rows, indent=2))
    print(json.dumps({"report": str(root/"loading.json")}))


if __name__ == "__main__":
    main()
