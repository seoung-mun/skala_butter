"""Detection sensitivity: how often and how fast each injection strength is caught, over many start years.

    python -m buttercast.sensitivity            # writes runtime/sensitivity.json and prints a markdown table

One year is an anecdote; the table aggregates every start year so the thresholds are judged on rates
(detection, fine-tune, promotion, false alarms in the normal weeks), not on a hand-picked period.
"""
import json
import os
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

CONFIGS = [("none", None), ("level_ramp", .05), ("level_ramp", .10), ("level_ramp", .15),
           ("variance", 1.5), ("variance", 2.0), ("variance", 3.0)]
YEARS = list(range(2005, 2024, 2))


def run(args):
    kind, factor, year = args
    os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")
    from .data import bundled_rows
    from .experiments import run_experiment, year_starts
    from .operations import Operations
    from .store import Store
    rows = bundled_rows()
    ops = Operations(Store(Path(tempfile.mkdtemp())))
    ops.store.append("datasets", dict(version="eu", rows=rows))
    try:
        run_experiment(ops, "s", rows, start=year_starts(rows, 65)[year], kind=kind, factor=factor)
    finally:
        ops.executor.shutdown(wait=True)
    r = json.loads((ops.store.root/"experiments"/"s"/"result.json").read_text())
    after = [s for s in r["steps"] if s["phase"] != "normal"]
    return dict(kind=kind, factor=factor, year=year, level=r["observed_level"], delay=r["detection_delay_observations"],
                normal_false_alarms=r["normal_alarm_observations"], finetunes=len(r["retraining"]),
                promoted=sum(t["promoted"] for t in r["retraining"]),
                changed_level1_weeks=sum(s["level"] >= 1 for s in after if s["phase"] == "changed"))


def table(results):
    lines = ["| 주입 | 세기 | 감지율(1단계 이상) | 파인튜닝 발생 | 승격 | 평균 감지 지연(주) | 정상 13주 오경보(주, 평균) |",
             "|---|---|---:|---:|---:|---:|---:|"]
    for kind, factor in CONFIGS:
        group = [r for r in results if r["kind"] == kind and r["factor"] == factor]
        delays = [r["delay"] for r in group if r["delay"] is not None]
        label = "없음" if kind == "none" else f"4주간 +{factor:.0%}" if kind == "level_ramp" else f"변동폭 ×{factor:g}"
        lines.append(f"| {kind} | {label} | {sum(r['level'] >= 1 for r in group)}/{len(group)} | "
                     f"{sum(r['finetunes'] > 0 for r in group)}/{len(group)} | {sum(r['promoted'] > 0 for r in group)}/{len(group)} | "
                     f"{sum(delays)/len(delays):.1f} | {sum(r['normal_false_alarms'] for r in group)/len(group):.1f} |"
                     if delays else
                     f"| {kind} | {label} | {sum(r['level'] >= 1 for r in group)}/{len(group)} | "
                     f"{sum(r['finetunes'] > 0 for r in group)}/{len(group)} | {sum(r['promoted'] > 0 for r in group)}/{len(group)} | — | "
                     f"{sum(r['normal_false_alarms'] for r in group)/len(group):.1f} |")
    return "\n".join(lines)


if __name__ == "__main__":
    jobs = [(kind, factor, year) for kind, factor in CONFIGS for year in YEARS]
    workers = int(sys.argv[1]) if len(sys.argv) > 1 else 4
    with ProcessPoolExecutor(workers) as pool:
        results = list(pool.map(run, jobs))
    out = Path("runtime/sensitivity.json")
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2))
    print(table(results))
