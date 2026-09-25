"""Run every remaining benchmark phase to completion, surviving plan usage limits.

Each phase is one run_claude_arms.py invocation (resumable: finished runs are skipped). When a
pass ends with runs that were not run — a usage limit, or the plan refusing requests — the
orchestrator waits and retries the same phase, until nothing is missing or the retry budget is
spent. Progress goes to stdout with timestamps, one line per pass.

Usage: python run_all.py <dab-root> [--wait-min 30] [--max-waits 60]
"""
import argparse
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
MODEL = "claude-sonnet-5"
PHASES = [  # (dataset, arms, queries)
    ("cve", "A", "1-10"),
    ("cve", "B", "1-10"),
    ("crmarenapro", "A,B,C", "1-13"),
]


def log(msg):
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}", flush=True)


def run_phase(dab, ds, arms, queries, runs):
    cmd = [sys.executable, str(HERE / "run_claude_arms.py"), str(dab), "--dataset", ds, "--model", MODEL,
           "--arms", arms, "--runs", str(runs), "--queries", queries, "--workers", "3"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    lines = r.stdout.splitlines()
    count = lambda tag: sum(1 for l in lines if l.startswith(tag))
    if r.returncode != 0:
        log(f"  runner exited {r.returncode}: {r.stderr[-400:]}")
    subprocess.run("docker rm -f $(docker ps -aq --filter name=autogen-code-exec) >/dev/null 2>&1", shell=True)
    return (count("ok"), count("skip"), count("NOT RUN"), count("FAILED"), count("AUTH FAILED"),
            r.returncode)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dab", type=Path)
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--wait-min", type=int, default=30)
    ap.add_argument("--max-waits", type=int, default=60)
    a = ap.parse_args()
    waits = 0
    for ds, arms, queries in PHASES:
        failed_passes = 0
        while True:
            ok, skip, not_run, failed, auth, rc = run_phase(a.dab, ds, arms, queries, a.runs)
            log(f"{ds} {arms}: ok={ok} already={skip} not_run={not_run} failed={failed} auth_failed={auth}")
            if auth:
                log("authentication failed: log in again (claude /login), then rerun; stopping")
                return 2
            if not_run:
                waits += 1
                if waits > a.max_waits:
                    log("retry budget spent; stopping")
                    return 1
                log(f"usage limit or refusal: waiting {a.wait_min} min (wait {waits}/{a.max_waits})")
                time.sleep(a.wait_min * 60)
                continue
            if (failed or rc) and failed_passes < 3:
                failed_passes += 1
                log("launch failures: retrying the phase")
                continue
            break
    log("all phases done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
