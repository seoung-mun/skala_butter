"""End-to-end: a real uvicorn process driven over HTTP (train → gate → approve → predict → drift
experiments → failure injection → restart recovery). See buttercast/verify_aiops.py for each check."""
import json
import socket
import subprocess
import sys

import pytest

pytestmark = pytest.mark.e2e


def test_real_server_full_aiops_loop(tmp_path):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    root = tmp_path/"verification"
    run = subprocess.run([sys.executable, "-m", "buttercast.verify_aiops", "--port", str(port), "--root", str(root)],
                         capture_output=True, text=True, timeout=1800)
    report = json.loads((root/"verification.json").read_text())
    assert run.returncode == 0 and report.get("passed"), run.stdout[-3000:]+run.stderr[-3000:]
    assert all(report["checks"].values())
    # Only the level-2 scenario (variance) fine-tunes; normal data and a short shock must not.
    assert {e["kind"]: bool(e["retraining"]) for e in report["experiments"]} == {
        "none": False, "level_ramp": False, "variance": True}
