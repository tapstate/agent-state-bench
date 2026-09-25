"""Classify every agnews article once, at ingest, with a language model: World, Sports, Business
or Science/Technology, from its title and description only.

This is an enrichment step outside Tapstate (Tapstate has no model step): its output is one more
source table, `article_category`, that the pipeline consolidates like any other. The benchmark's
own labels are never read here; eval_agnews_labels.py grades the result against the public AG
News labels afterwards.

Runs `claude -p` (no tools, no settings) on batches of articles; results are appended to a TSV
file as each batch finishes, so an interrupted run resumes where it stopped.

Usage: python classify_agnews.py <out.tsv> [--model claude-haiku-4-5-20251001] [--batch 200] [--workers 4]
       python classify_agnews.py <out.tsv> --load     # write the TSV into agnewssrc.article_category
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import psycopg2
from psycopg2.extras import execute_values

DSN = "host=127.0.0.1 port=55432 user=postgres password=secret dbname=agnewssrc"
CODES = {"W": "World", "S": "Sports", "B": "Business", "T": "Science/Technology"}
CLAUDE = os.environ.get("CLAUDE_BIN") or shutil.which("claude") or str(Path.home() / ".local/bin/claude")
PROMPT = """Classify each news article below into exactly one category:
W = World (international news, politics, conflicts, diplomacy)
S = Sports
B = Business (companies, markets, economy)
T = Science/Technology (science, technology, computing, health research, space)

Each input line is: id<TAB>title<TAB>description
Answer with one line per article, in the same order: id<TAB>letter. Output nothing else.

"""


class AuthFailed(Exception):
    pass


def child_env():
    e = os.environ.copy()
    for k in list(e):
        if k.startswith("CLAUDE") or k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
            e.pop(k)
    return e


def classify(batch, model):
    lines = "\n".join(f"{i}\t{t}\t{d}".replace("\r", " ") for i, t, d in batch)
    r = subprocess.run([CLAUDE, "-p", "--output-format", "json", "--model", model, "--tools", "",
                        "--setting-sources", "", "--strict-mcp-config", "--no-session-persistence",
                        "--max-turns", "1"], input=PROMPT + lines, capture_output=True, text=True,
                       env=child_env(), cwd="/tmp")
    try:
        res = json.loads(r.stdout)
    except json.JSONDecodeError:
        return {}
    text = str(res.get("result") or "")
    if res.get("is_error") and re.search(r"authenticat|oauth|log ?in", text, re.I):
        raise AuthFailed(text[:200])
    out = {}
    for line in text.splitlines():
        m = re.match(r"^\s*(\d+)\s*\t\s*([WSBT])\b", line)
        if m:
            out[int(m[1])] = m[2]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out", type=Path)
    ap.add_argument("--model", default="claude-haiku-4-5-20251001")
    ap.add_argument("--batch", type=int, default=200)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--load", action="store_true")
    a = ap.parse_args()
    con = psycopg2.connect(DSN)
    con.autocommit = True
    cur = con.cursor()
    done = {}
    if a.out.exists():
        for line in a.out.read_text().splitlines():
            i, c = line.split("\t")
            done[int(i)] = c
    if a.load:
        cur.execute("drop table if exists article_category")
        cur.execute("create table article_category (article_id bigint primary key, category text, method text)")
        execute_values(cur, "insert into article_category values %s",
                       [(i, CODES[c], f"llm:{a.model}") for i, c in sorted(done.items())], page_size=5000)
        cur.execute("alter table article_category replica identity full")
        print(f"loaded {len(done)} categories")
        return 0
    cur.execute("select article_id, title, description from articles order by article_id")
    todo = [r for r in cur.fetchall() if r[0] not in done]
    for attempt in range(3):
        if not todo:
            break
        batches = [todo[i:i + a.batch] for i in range(0, len(todo), a.batch)]
        print(f"pass {attempt + 1}: {len(todo)} articles in {len(batches)} batches", flush=True)
        with ThreadPoolExecutor(a.workers) as ex, a.out.open("a") as f:
            for n, got in enumerate(ex.map(lambda b: classify(b, a.model), batches), 1):
                for i, c in got.items():
                    if i not in done:
                        done[i] = c
                        f.write(f"{i}\t{c}\n")
                f.flush()
                if n % 25 == 0:
                    print(f"  {n}/{len(batches)} batches, {len(done)} classified", flush=True)
        todo = [r for r in todo if r[0] not in done]
    print(f"classified {len(done)}; missing {len(todo)}")
    return 0 if not todo else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AuthFailed as e:
        print(f"authentication failed: {e}; log in again and rerun")
        sys.exit(2)
