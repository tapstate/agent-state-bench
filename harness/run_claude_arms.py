"""Run the arms with Claude Code as the data agent (headless `claude -p`), on a Claude plan login.

The agent gets exactly DAB's four tools, through dab_mcp_server.py, and nothing else: no
built-in tools, no MCP servers but that one, no user/project settings. Its system prompt is
DAB's own (the Claude variant, with the storage-key example matched to this server's keys),
and the user message is DAB's QUERY + DATABASE DESCRIPTION. Arms, datasets and hints are
those of run_arms.py.

Each run writes query_<dataset>/queryN/logs/data_agent/<arm>_cc-<model>_r<k>/ with
claude_result.json (Claude Code's JSON result: usage, num_turns, total_cost_usd),
tool_calls.jsonl, answer.json and a DAB-shaped final_agent.json. Resumable like run_arms.py.
A run that stops on a plan usage limit is not recorded as finished, so re-running retries it.

Usage: python run_claude_arms.py <dab-root> --model claude-sonnet-5 [--arms A,B,C]
                                 [--runs 5] [--queries 1-13] [--workers 3]
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from run_arms import ARMS, HERE, arms_for, env, parse_range, warm

TOOLS = ["query_db", "list_db", "execute_python", "return_answer"]
# Resolved once, absolutely: a background shell may not carry the interactive PATH.
CLAUDE = os.environ.get("CLAUDE_BIN") or shutil.which("claude") or str(Path.home() / ".local/bin/claude")


def build_prompts(dab, ds, q, hints):
    sys.path.insert(0, str(dab))
    from common_scaffold.prompts import prompt_builder
    base = dab / f"query_{ds}"
    desc = (base / "db_description.txt").read_text().strip()
    if hints:
        desc += "\n\n" + (base / "db_description_withhint.txt").read_text().strip()
    query = json.loads((base / f"query{q}" / "query.json").read_text())
    query = query["query"] if isinstance(query, dict) else query
    system, user = prompt_builder.init_messages(query, desc, "claude")
    system_text = (system["content"].replace('"toolu_1"', '"call_1"').replace("var_toolu_1", "var_call_1")
                   + "\n\nThe tools are provided by the MCP server `dab`: query_db, list_db, execute_python "
                     "and return_answer appear as mcp__dab__query_db, mcp__dab__list_db, "
                     "mcp__dab__execute_python and mcp__dab__return_answer.")
    return system_text, user["content"]


AUTH_FAILED, NOT_RUN, FINISHED = "auth_failed", "not_run", "finished"
AUTH_RE = re.compile(r"authenticat|oauth|not logged in|log ?in again|invalid api key|401", re.I)
REFUSED_RE = re.compile(r"limit|403|429|rate.?limit|overloaded|not allowed", re.I)


def classify(res):
    """What one `claude -p --output-format json` result means for the batch.

    AUTH_FAILED: the login is gone; retrying later cannot succeed, so the batch must stop.
    NOT_RUN: refused before doing work (usage limit, overload, access); retry after a wait.
    FINISHED: the agent ran; its answer, or lack of one, is scored.
    """
    text = str(res.get("result") or "")
    if res.get("is_error") and AUTH_RE.search(text):
        return AUTH_FAILED
    if res.get("is_error") and (REFUSED_RE.search(text) or (res.get("num_turns") or 0) <= 1):
        return NOT_RUN
    return FINISHED


def child_env():
    e = env()
    for k in list(e):  # a nested session must not inherit the parent Claude Code session
        if k.startswith("CLAUDE") or k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
            e.pop(k)
    return e


# Twice the longest finished run seen (66 min); only a stalled session reaches it.
TIMEOUT_S = 7200


def run_one(dab, arm, model, q, k, max_turns, arms=ARMS, timeout=TIMEOUT_S):
    ds, hints = arms[arm]
    root = f"{arm}_cc-{model}_r{k}"
    out = dab / f"query_{ds}" / f"query{q}" / "logs" / "data_agent" / root
    if (out / "final_agent.json").exists():
        return f"skip {root} q{q}"
    if out.exists():
        subprocess.run(["rm", "-rf", str(out)], check=True)
    out.mkdir(parents=True)
    system_text, user_text = build_prompts(dab, ds, q, hints)
    (out / "system_prompt.txt").write_text(system_text)
    (out / "mcp.json").write_text(json.dumps({"mcpServers": {"dab": {
        "command": sys.executable,
        "args": [str(HERE / "dab_mcp_server.py"), "--dab", str(dab), "--dataset", ds, "--run-dir", str(out)],
        "env": {k2: v for k2, v in child_env().items()
                if k2 in ("PATH", "HOME", "DOCKER_HOST", "MONGO_URI", "DAB_PERSISTENT_DBS")
                or k2.startswith("PG_")},
    }}}))
    cmd = [CLAUDE, "-p", "--output-format", "json", "--model", model,
           "--system-prompt-file", str(out / "system_prompt.txt"),
           "--tools", "", "--strict-mcp-config", "--mcp-config", str(out / "mcp.json"),
           "--allowedTools", ",".join(f"mcp__dab__{t}" for t in TOOLS),
           "--setting-sources", "", "--max-turns", str(max_turns), "--no-session-persistence"]
    start = time.time()
    try:
        r = subprocess.run(cmd, input=user_text, cwd=out, env=child_env(), capture_output=True, text=True,
                           timeout=timeout)
    except subprocess.TimeoutExpired:
        # a stalled session, not an answer: no final_agent.json, so a re-run retries it
        return f"FAILED {root} q{q}: timed out after {timeout}s"
    except OSError as e:  # one failed launch must not end the whole batch
        return f"FAILED {root} q{q}: {e}"
    duration = time.time() - start
    (out / "claude_stdout.txt").write_text(r.stdout)
    (out / "claude_stderr.txt").write_text(r.stderr)
    try:
        res = json.loads(r.stdout)
    except json.JSONDecodeError:
        return f"FAILED {root} q{q}: rc={r.returncode} {(r.stderr or r.stdout)[-300:]}"
    (out / "claude_result.json").write_text(json.dumps(res, indent=2))
    text = str(res.get("result") or "")
    outcome = classify(res)
    if outcome == AUTH_FAILED:
        # waiting does not fix an expired login: the caller stops and asks for a new one
        return f"AUTH FAILED {root} q{q}: {text[:200]}"
    if outcome == NOT_RUN:
        # a usage limit or an access refusal before any work: not a finished run, so it gets
        # no final_agent.json and a re-run retries it
        return f"NOT RUN {root} q{q}: {text[:200]}"
    answer_file = out / "answer.json"
    answer = json.loads(answer_file.read_text())["answer"] if answer_file.exists() else ""
    (out / "final_agent.json").write_text(json.dumps({
        "final_result": answer,
        "terminate_reason": "return_answer" if answer_file.exists() else (res.get("subtype") or "no_answer"),
        "llm_call_count": res.get("num_turns"),
        "duration": duration,
        "agent": "claude-code",
        "deployment_name": model,
    }, indent=2))
    return f"ok {root} q{q} turns={res.get('num_turns')} cost=${res.get('total_cost_usd')}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dab", type=Path)
    ap.add_argument("--model", required=True)
    ap.add_argument("--dataset", default="crmarenapro")
    ap.add_argument("--arms", default="A,B,C")
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--queries", default="1-13")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--max-turns", type=int, default=100)
    ap.add_argument("--timeout", type=int, default=TIMEOUT_S, help="wall-clock seconds per run")
    a = ap.parse_args()
    dab = a.dab.resolve()
    arms = a.arms.split(",")
    table = arms_for(a.dataset)
    warm(dab, sorted({table[x][0] for x in arms}))
    jobs = [(x, q, k) for k in range(a.runs) for x in arms for q in parse_range(a.queries)]
    with ThreadPoolExecutor(a.workers) as pool:
        for line in pool.map(lambda j: run_one(dab, j[0], a.model, j[1], j[2], a.max_turns, table, a.timeout), jobs):
            print(line, flush=True)


if __name__ == "__main__":
    main()
