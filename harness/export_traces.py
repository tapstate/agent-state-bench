"""Package the run traces for publication, keeping what a reader needs to check a result.

For every run directory (logs/data_agent/<setup>_<model>_r<k>) it keeps:
  final_agent.json   the answer and how the run ended
  tool_calls.jsonl   every tool call the agent made, with arguments and result sizes
  run.json           turns, tokens and API-equivalent cost from Claude Code's result
and drops everything else: the MCP configuration (local environment and paths), Claude Code's raw
output (session identifiers), the system prompt (DAB's prompt text, published by DAB), and the
Python sandbox's working files. Local paths in what is kept are replaced by `<dab>` and `<home>`.

Nothing here decides whether the traces may be published: DAB's data appears in tool results.

Usage: python export_traces.py <dab-root> <out-dir>     # writes <out-dir>/<dataset>.tar.gz
"""
import io
import json
import re
import sys
import tarfile
from pathlib import Path

KEEP = ("final_agent.json", "tool_calls.jsonl")
RESULT_FIELDS = ("num_turns", "duration_ms", "duration_api_ms", "total_cost_usd", "usage", "stop_reason",
                 "subtype", "is_error")


def redact(text, dab):
    text = text.replace(str(dab), "<dab>")
    return re.sub(r"/(Users|home)/[^/\s\"']+", "<home>", text)


def main():
    dab, out = Path(sys.argv[1]).resolve(), Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    by_dataset = {}
    for run in sorted(dab.glob("query_*/query*/logs/data_agent/*")):
        if (run / "final_agent.json").exists():
            by_dataset.setdefault(run.parents[3].name[len("query_"):], []).append(run)
    for ds, runs in sorted(by_dataset.items()):
        with tarfile.open(out / f"{ds}.tar.gz", "w:gz") as tar:
            for run in runs:
                rel = run.relative_to(dab)
                files = {name: redact((run / name).read_text(), dab) for name in KEEP if (run / name).exists()}
                result = run / "claude_result.json"
                if result.exists():
                    res = json.loads(result.read_text())
                    files["run.json"] = json.dumps({k: res.get(k) for k in RESULT_FIELDS}, indent=2)
                for name, text in files.items():
                    data = text.encode()
                    info = tarfile.TarInfo(str(rel / name))
                    info.size = len(data)
                    tar.addfile(info, io.BytesIO(data))
        print(f"{ds}: {len(runs)} runs -> {out / (ds + '.tar.gz')}")


if __name__ == "__main__":
    main()
