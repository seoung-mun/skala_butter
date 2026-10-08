"""Run weekly butter price learning with persistent job and epoch records."""
import argparse
import json
import time
from pathlib import Path

import numpy as np

from .data import AUDIT, load_eu, parse_weekly_prices, version
from .metrics import stamp
from .models import Bundle, train_bundle
from .operations import Operations
from .store import Store


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--epochs", type=int, default=20)
    parser.add_argument("--input", type=Path, help="팀 제공 주간 가격 CSV; EU 데이터와 혼합하지 않음")
    args = parser.parse_args()
    if not 1 <= args.epochs <= 100:
        parser.error("epochs must be between 1 and 100")
    prefix = "provided" if args.input else "eu"
    root = Path(__file__).resolve().parents[1] / "runtime" / f"{prefix}-training"
    store = Store(root)
    rows = parse_weekly_prices(args.input.read_text(encoding="utf-8-sig")) if args.input else load_eu(AUDIT / "eu-butter-weekly.csv")
    synthetic = None if args.input else False
    data_version = version(rows)
    store.append("datasets", dict(version=data_version, rows=rows, imported_at=stamp(),
                                  source=rows[0]["source"], synthetic=synthetic))
    ops = Operations(store)

    def work(job_id):
        model_version = f"{prefix}-{job_id}"
        folder = root / "models" / model_version
        ops.event(job_id, "ingest", "succeeded")
        ops.event(job_id, "train", "running")

        def record_epoch(record):
            store.append("epochs", dict(job_id=job_id, at=stamp(), **record))
            print(json.dumps(record), flush=True)

        metadata = train_bundle(rows, folder, epochs=args.epochs, interval_days=7,
                                on_epoch=record_epoch, synthetic=synthetic)
        ops.event(job_id, "train", "succeeded")
        ops.event(job_id, "artifact_check", "running")
        sample = metadata["reference"]  # the served weights' own reference (refit differs from the scored model)
        start = time.perf_counter()
        result = Bundle(folder).predict([item[0] for item in sample["x"]])
        inference_ms = (time.perf_counter()-start)*1000
        if not np.isclose(result["prediction"], sample["prediction"], rtol=1e-5):
            raise ValueError("저장·재로딩 후 예측값 불일치")
        ops.event(job_id, "artifact_check", "succeeded")
        store.append("models", dict(version=model_version, data_version=data_version, at=stamp(), **metadata))
        ops.event(job_id, "validation", "succeeded" if metadata["validation_passed"] else "rejected")
        summary = dict(version=model_version, data_version=data_version, actual_rows=len(rows),
                       synthetic=synthetic, source=rows[0]["source"],
                       provenance_verified=not bool(args.input),
                       unit=metadata["unit"], comparisons=metadata["comparisons"],
                       partition_counts=metadata["partition_counts"],
                       validation_passed=metadata["validation_passed"], artifact_reload_verified=True,
                       load_and_first_inference_ms=inference_ms, mlflow_run_id=metadata["mlflow_run_id"],
                       deployment="not_approved", at=stamp(),
                       availability="과거 발표일 미확인: 관측일+7일 가정의 역사적 재현",
                       horizon="가정한 발행일 기준 28일 후; horizon_anchor=available_on")
        (folder / "run-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
        return summary

    job = ops.submit("training", work)
    print(json.dumps(dict(job_id=job["id"], rows=len(rows), store=str(root))), flush=True)
    ops.executor.shutdown(wait=True)
    finished = next(row for row in ops.jobs() if row["id"] == job["id"])
    print(json.dumps(finished, ensure_ascii=False, indent=2), flush=True)
    if finished["status"] != "succeeded":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
