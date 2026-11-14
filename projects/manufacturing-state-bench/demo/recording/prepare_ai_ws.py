"""Demo "three ways" beat, the AI path: export what an assistant needs to draft a Tapstate pipeline,
read-only, into recording/ai_ws/:

  source/*.tap.yml   the workspace's source definitions (no pipelines: the assistant must not copy one)
  schema/*.txt       the schemas Tapstate discovered for each source (`tapstate schema <id>`)
  grammar/*.txt      the pipeline grammar, as the CLI documents it offline (`tapstate explain <path>`)

Nothing is written to the server, and no token is created. Needs TAPSTATE_CLI, TAPSTATE_SERVER and
TAPSTATE_PASSWORD (env.sh).

Usage: python recording/prepare_ai_ws.py
"""
import os
import re
import shutil
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parents[1] / "dab" / "tapstate" / "source"
OUT = HERE / "ai_ws"
CLI = os.environ.get("TAPSTATE_CLI", "tapstate")


def cli(*args, server=False):
    cmd = [CLI] + (["-c", os.environ["TAPSTATE_SERVER"], "-u", "admin"] if server else []) + list(args)
    r = subprocess.run(cmd, capture_output=True, text=True)
    return "\n".join(ln for ln in (r.stdout + r.stderr).splitlines() if "SLF4J" not in ln)


def grammar(path, seen, depth=0):
    """`tapstate explain` for path and, recursively, every object/array field it lists."""
    if path in seen or depth > 5:
        return
    seen.add(path)
    text = cli("explain", path)
    if text.startswith("no such grammar field path"):
        return
    (OUT / "grammar" / f"{path}.txt").write_text(text + "\n")
    m = re.search(r"FIELDS:\n(.*)", text, re.S)
    for line in (m.group(1).splitlines() if m else []):
        parts = line.split()
        if len(parts) >= 2 and parts[1] in ("object", "array", "enum", "const"):
            grammar(f"{path}.{parts[0]}", seen, depth + 1)


def main():
    if OUT.exists():
        shutil.rmtree(OUT)
    for d in ("source", "schema", "grammar", "pipeline"):
        (OUT / d).mkdir(parents=True)
    ids = []
    for f in sorted(SRC.glob("*.tap.yml")):
        shutil.copy(f, OUT / "source" / f.name)
        ids.append(f.name.split(".")[0])
    for sid in ids:
        summary = cli("schema", sid, server=True)
        tables = re.findall(r"^  (\w+)\s+\d+ fields", summary, re.M)
        detail = [cli("schema", sid, t, server=True) for t in tables]
        (OUT / "schema" / f"{sid}.txt").write_text("\n\n".join([summary] + detail) + "\n")
    seen = set()
    for root in ("pipeline", "transform", "view"):
        grammar(root, seen)
    print(f"{OUT}: {len(ids)} sources, {len(ids)} schemas, {len(seen)} grammar pages")


if __name__ == "__main__":
    main()
