"""Run the DAB agent over arms x queries x runs, in parallel, resumable.

Arms (same harness, tools, iteration cap and model; only data and description differ):
  A  raw crmarenapro databases, no hints
  B  raw crmarenapro databases, with DAB's hints
  C  consolidated state (crmarenapro_consolidated), no hints

Each run writes query_<dataset>/queryN/logs/data_agent/<arm>_<model>_r<k>/ in the DAB
checkout; a run whose final_agent.json exists is skipped, so re-running resumes.

Usage: python run_arms.py <dab-root> --model claude-sonnet-5 [--arms A,B,C] [--runs 5]
                          [--queries 1-13] [--workers 6]
"""
import argparse
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
def arms_for(dataset):
    """arm -> (DAB dataset directory, use hints)."""
    # C2: the consolidated state with question-shaped fields removed, where such a variant exists
    return {"A": (dataset, False), "B": (dataset, True), "C": (f"{dataset}_consolidated", False),
            "C2": (f"{dataset}_consolidated_v2", False)}


ARMS = arms_for("crmarenapro")


def env():
    e = os.environ.copy()
    e["PATH"] = f"{HERE / 'bin'}:{e['PATH']}"
    e.setdefault("DAB_PERSISTENT_DBS", "1")
    e.setdefault("PG_HOST", "127.0.0.1")
    e.setdefault("PG_PORT", "55432")
    e.setdefault("PG_USER", "postgres")
    e.setdefault("PG_PASSWORD", "secret")
    e.setdefault("DOCKER_HOST", f"unix://{Path.home()}/.docker/run/docker.sock")
    e.setdefault("MONGO_URI", "mongodb://127.0.0.1:27017/?directConnection=true")
    return e


def parse_range(s):
    out = []
    for part in s.split(","):
        a, _, b = part.partition("-")
        out += range(int(a), int(b or a) + 1)
    return out


def warm(dab, datasets):
    """Load each dataset's server-side databases once, before parallel runs race to do it."""
    code = (
        "import sys; sys.path.insert(0, '.')\n"
        "from common_scaffold.tools.db_utils.db_config import load_db_clients\n"
        "import common_scaffold.tools.db_utils.mongo_utils as m, common_scaffold.tools.db_utils.postgres_utils as p\n"
        "for c in load_db_clients(sys.argv[1]).values():\n"
        "    if c['db_type'] == 'mongo': m.load_db(c['dump_folder'], c['db_name'])\n"
        "    elif c['db_type'] == 'postgres': p.load_db(c['sql_file'], c['db_name'])\n"
    )
    for ds in datasets:
        subprocess.run([sys.executable, "-c", code, str(dab / f"query_{ds}" / "db_config.yaml")],
                       cwd=dab, env=env(), check=True)


def run_one(dab, arm, model, q, k, iterations, arms=ARMS):
    ds, hints = arms[arm]
    root = f"{arm}_{model}_r{k}"
    out = dab / f"query_{ds}" / f"query{q}" / "logs" / "data_agent" / root
    if (out / "final_agent.json").exists():
        return f"skip {root} q{q}"
    if out.exists():
        subprocess.run(["rm", "-rf", str(out)], check=True)  # an interrupted run: redo it
    cmd = [sys.executable, "run_agent.py", "--dataset", ds, "--query_id", str(q), "--llm", model,
           "--iterations", str(iterations), "--root_name", root]
    if hints:
        cmd.append("--use_hints")
    r = subprocess.run(cmd, cwd=dab, env=env(), capture_output=True, text=True)
    status = "ok" if (out / "final_agent.json").exists() else f"FAILED rc={r.returncode}: {r.stderr[-300:]}"
    return f"{status} {root} q{q}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dab", type=Path)
    ap.add_argument("--model", required=True)
    ap.add_argument("--dataset", default="crmarenapro")
    ap.add_argument("--arms", default="A,B,C")
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--queries", default="1-13")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--iterations", type=int, default=100)
    a = ap.parse_args()
    dab = a.dab.resolve()
    arms = a.arms.split(",")
    table = arms_for(a.dataset)
    warm(dab, sorted({table[x][0] for x in arms}))
    jobs = [(x, q, k) for k in range(a.runs) for x in arms for q in parse_range(a.queries)]
    with ThreadPoolExecutor(a.workers) as pool:
        for line in pool.map(lambda j: run_one(dab, j[0], a.model, j[1], j[2], a.iterations, table), jobs):
            print(line, flush=True)


if __name__ == "__main__":
    main()
