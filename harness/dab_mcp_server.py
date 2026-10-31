"""DAB's four agent tools as a stdio MCP server, so Claude Code can be the data agent.

It reuses DAB's own tool classes (QueryDBTool, ListDBTool, ExecTool, ReturnAnswerTool) and
reproduces DataAgent's result handling: every successful result is stored under a key
(`var_call_<n>`), results longer than 10,000 characters are written to a file and previewed,
and execute_python receives the stored results as variables. Tool outcomes use DataAgent's
own message templates, so the agent sees the same text it would in DAB's loop.

One server process per agent run. It writes into --run-dir:
  tool_calls.jsonl  every call with its arguments, outcome and duration
  answer.json       the argument of the first return_answer call

Usage (from an MCP config): python dab_mcp_server.py --dab <dab-root> --dataset <name> --run-dir <dir>
"""
import argparse
import asyncio
import json
import os
import signal
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--dab", type=Path, required=True)
ap.add_argument("--dataset", required=True)
ap.add_argument("--run-dir", type=Path, required=True)
args = ap.parse_args()

sys.path.insert(0, str(args.dab))
from mcp.server.mcpserver import MCPServer  # noqa: E402

from common_scaffold.DataAgent import (  # noqa: E402
    FAIL_TOOL_PREVIEW_TMPL, FAIL_TOOL_RESULT_TMPL, SUCCESS_TOOL_PREVIEW_TMPL, SUCCESS_TOOL_RESULT_TMPL)
from common_scaffold.prompts.prompt_builder import PREVIEW_LENGTH  # noqa: E402

RUN = args.run_dir.resolve()
WORK = RUN / "exec_tool_work_dir"
STORAGE = WORK / "file_storage"
STORAGE.mkdir(parents=True, exist_ok=True)
DB_CONFIG = args.dab / f"query_{args.dataset}" / "db_config.yaml"

# DAB's ExecTool drives its own asyncio loop, so every DAB tool runs on one worker thread,
# away from the MCP server's event loop.
worker = ThreadPoolExecutor(max_workers=1)
state = {"tools": None, "storage": {}, "n": 0}


def _init_tools():
    from common_scaffold.tools.ListDBTool import ListDBTool
    from common_scaffold.tools.QueryDBTool import QueryDBTool
    from common_scaffold.tools.ReturnAnswerTool import ReturnAnswerTool
    log = RUN / "dab_tool_log.jsonl"
    state["tools"] = {
        "query_db": QueryDBTool(log_path=log, name="query_db", db_config_path=DB_CONFIG, check_load=True),
        "list_db": ListDBTool(log_path=log, name="list_db", db_config_path=DB_CONFIG, check_load=False),
        "return_answer": ReturnAnswerTool(log_path=log, name="return_answer"),
    }


def _call(name, tool_args):
    if state["tools"] is None:
        _init_tools()
    state["n"] += 1
    call_id = f"call_{state['n']}"
    if name == "execute_python":
        if name not in state["tools"]:  # the sandbox container starts on first use only
            from common_scaffold.tools.ExecTool import ExecTool
            state["tools"][name] = ExecTool(log_path=RUN / "dab_tool_log.jsonl", name=name, work_dir=WORK,
                                            timeout=600)
        tool_args = dict(tool_args, env=state["storage"].copy())
    start = time.time()
    try:
        out = state["tools"][name].exec(tool_args)  # {"success": bool, "result": ...}
    except Exception as e:  # DAB's FatalError escapes exec(); report it rather than drop the call
        out = {"success": False, "result": f"{type(e).__name__}: {e}"}
    serialized = json.dumps(out["result"])
    if out["success"]:
        if name == "return_answer" and not (RUN / "answer.json").exists():
            (RUN / "answer.json").write_text(json.dumps({"answer": tool_args.get("answer") or ""}))
        key = f"var_{call_id}"
        if len(serialized) > PREVIEW_LENGTH:
            path = STORAGE / f"{call_id}.json"
            path.write_text(json.dumps(out["result"], indent=2))
            state["storage"][key] = os.path.join("file_storage", path.name)
            text = (SUCCESS_TOOL_PREVIEW_TMPL.replace("{tool_name}", name).replace("{result_key}", key)
                    .replace("{preview_length}", str(PREVIEW_LENGTH))
                    .replace("{tool_result_preview}", serialized[:PREVIEW_LENGTH]))
        else:
            state["storage"][key] = out["result"]
            text = (SUCCESS_TOOL_RESULT_TMPL.replace("{tool_name}", name).replace("{result_key}", key)
                    .replace("{tool_result}", serialized))
    elif len(serialized) > PREVIEW_LENGTH:
        text = (FAIL_TOOL_PREVIEW_TMPL.replace("{tool_name}", name)
                .replace("{tool_result_preview}", serialized[:PREVIEW_LENGTH]))
    else:
        text = FAIL_TOOL_RESULT_TMPL.replace("{tool_name}", name).replace("{tool_result}", serialized)
    with (RUN / "tool_calls.jsonl").open("a") as f:
        f.write(json.dumps({"id": call_id, "tool": name, "args": {k: v for k, v in tool_args.items() if k != "env"},
                            "success": out["success"], "result_chars": len(serialized),
                            "duration_s": round(time.time() - start, 3)}) + "\n")
    return text


async def _run(name, tool_args):
    return await asyncio.get_running_loop().run_in_executor(worker, _call, name, tool_args)


server = MCPServer(name="dab")


@server.tool(description="Execute a query (SQL or MongoDB query) on a specific database and return the result "
                         "as a list of records. For MongoDB, `query` is a JSON string with a `collection` field and "
                         "optional `filter`, `projection` and `limit` fields; or `collection` plus `pipeline` (a list "
                         "of aggregation stages) to run an aggregation instead.")
async def query_db(db_name: str, query: str) -> str:
    return await _run("query_db", {"db_name": db_name, "query": query})


@server.tool(description="List the tables or collections of a database.")
async def list_db(db_name: str) -> str:
    return await _run("list_db", {"db_name": db_name})


@server.tool(description="Run Python code in a Python 3.12 environment with pandas and pyarrow. Stored results "
                         "are available as variables named by their storage keys.")
async def execute_python(code: str) -> str:
    return await _run("execute_python", {"code": code})


@server.tool(description="Finish and return the final answer (plain text).")
async def return_answer(answer: str) -> str:
    return await _run("return_answer", {"answer": answer})


def _clean_up(*_):
    """Stop the sandbox container. Claude Code ends the server with a signal, not stdin EOF, so
    this runs from the signal handler too; without it every run leaves a container running."""
    if state["tools"]:  # on the thread that owns the exec tool's loop
        tools, state["tools"] = state["tools"], None
        worker.submit(lambda: [t.clean_up() for t in tools.values()]).result(timeout=60)
    if _:
        os._exit(0)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _clean_up)
    signal.signal(signal.SIGINT, _clean_up)
    try:
        server.run("stdio")
    finally:
        _clean_up()
