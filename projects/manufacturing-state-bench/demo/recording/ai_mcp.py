"""Demo "three ways" beat, the AI path through MCP, run for real on Tapstate 0.8.0: Claude Code (on the
Claude Code login on this machine) is connected to the 0.8.0 demo server through `tapstate mcp` (read
tools only: no --allow-write, and a read-scoped machine token). From one short prompt it looks up the
sources and the schemas Tapstate discovered, writes the pipeline as a .tap.yml, and checks it with
`tapstate validate` (the CLI, offline, on its own workspace), fixing what validation reports. Then the
human check on screen: our own `tapstate validate` and `tapstate desc` of the draft. Nothing is applied
to the server.

The token is read from $TAPSTATE_DEMO_TOKEN_FILE (default ~/.tapstate-demo-mcp.txt, mode 600: the
output of `token create --scope read`, whose "token <value>" line is the one-time bearer value), put only into a private MCP config outside the workspace, deleted after the
run, and never printed.

Prints the work live (for record_live-style capture), and writes ai_mcp_ws/result.json: the draft,
every validation Claude ran, our check, tokens and cost.

Usage (from demo/, env.sh and env08.sh sourced): python recording/ai_mcp.py [zh|en]
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
WS = HERE / "ai_mcp_ws"
sys.path.insert(0, str(HERE.parents[3] / "harness"))
from run_claude_arms import CLAUDE, child_env  # noqa: E402

PROMPT = {
    "zh": "把产品、价格、库存和供应商嵌套成一个产品文档。",
    "en": "Nest products, prices, stock and vendors into one product document.",
}
BRIEF = ("\n\nWrite this as a Tapstate pipeline for the server you are connected to (Tapstate 0.8.0). Use the "
         "Tapstate MCP tools to find its sources and the schemas Tapstate discovered for them. Put the source "
         "definitions you use in source/ (as Tapstate returns them) and the pipeline, id product_state_ai, in "
         "pipeline/product_state_ai.tap.yml. Check your work with `tapstate validate .` (and `tapstate explain "
         "<field>` for the grammar) and fix what it reports until it is valid. Do not apply anything to the "
         "server. Finish with two sentences on your design.")
TEXT = {
    "zh": {"title": "AI 辅助（经 MCP）：Claude Code 连接 Tapstate 0.8.0，根据一句话起草管道", "prompt": "提示词",
           "conn": "连接：tapstate mcp（只读工具 · 只读令牌）→ Tapstate 0.8.0 演示服务器；Claude 不能发布任何内容",
           "done": "Claude 完成", "check": "人工确认：用 Tapstate CLI 校验草稿（不发布到服务器）",
           "usage": "token {t:,}（输入 {i}，缓存写入 {cw:,}，缓存读取 {cr:,}，输出 {o:,}） · 成本 ${c:.2f} · 用时 {s:.0f} 秒",
           "writes": "写入", "reads": "读取", "runs": "运行", "mcp": "MCP"},
    "en": {"title": "AI-assisted (via MCP): Claude Code connected to Tapstate 0.8.0 drafts the pipeline from one sentence",
           "prompt": "prompt",
           "conn": "connection: tapstate mcp (read tools · read-scoped token) → the Tapstate 0.8.0 demo server; Claude cannot apply anything",
           "done": "Claude done", "check": "Human check: validate the draft with the Tapstate CLI (nothing applied)",
           "usage": "tokens {t:,} (input {i}, cache write {cw:,}, cache read {cr:,}, output {o:,}) · cost ${c:.2f} · {s:.0f} s",
           "writes": "writes", "reads": "reads", "runs": "runs", "mcp": "MCP"},
}
B, C, D, G, Y, M, RED, R = "\033[1m", "\033[36m", "\033[2m", "\033[32m", "\033[33m", "\033[35m", "\033[31m", "\033[0m"


def cli():
    return os.environ.get("TAPSTATE_CLI", "tapstate")


def tapstate(*args):
    r = subprocess.run([cli(), *args], capture_output=True, text=True, cwd=WS)
    return r.returncode, "\n".join(ln for ln in (r.stdout + r.stderr).splitlines() if "SLF4J" not in ln)


def read_token():
    f = Path(os.environ.get("TAPSTATE_DEMO_TOKEN_FILE", Path.home() / ".tapstate-demo-mcp.txt"))
    raw = f.read_text().strip()
    for ln in raw.splitlines():          # `token create` text output: "created <id> READ", "token <value>"
        if ln.startswith("token ") and ln.split(None, 1)[1].strip():
            return ln.split(None, 1)[1].strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    for key in ("token", "secret", "value", "accessToken", "access_token"):
        if isinstance(data.get(key), str) and data[key]:
            return data[key]
    raise SystemExit(f"no token field in {f} (fields: {sorted(data)})")


def short(v, n=90):
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
    s = " ".join(s.split())
    return s if len(s) <= n else s[:n - 1] + "…"


def main():
    lang = sys.argv[1] if len(sys.argv) > 1 else "zh"
    T = TEXT[lang]
    if WS.exists():
        shutil.rmtree(WS)
    (WS / "source").mkdir(parents=True)
    (WS / "pipeline").mkdir()
    bindir = Path(tempfile.mkdtemp(prefix="tapcli"))
    (bindir / "tapstate").symlink_to(cli())
    cfgdir = Path(tempfile.mkdtemp(prefix="tapmcp"))      # private, outside the workspace Claude can read
    cfg = cfgdir / "mcp.json"
    server = "http://" + os.environ["TAPSTATE_SERVER"]
    old = os.umask(0o077)
    cfg.write_text(json.dumps({"mcpServers": {"tapstate": {
        "command": cli(), "args": ["mcp", f"--server={server}"], "env": {"TAPSTATE_TOKEN": read_token()}}}}))
    os.umask(old)
    print(f"{B}=== {T['title']} ==={R}")
    print(f"{D}{T['conn']}{R}\n")
    print(f"{T['prompt']}: {Y}{PROMPT[lang]}{R}\n", flush=True)
    time.sleep(1)
    allowed = "mcp__tapstate,Read,Glob,Grep,Write,Edit,Bash(tapstate validate:*),Bash(tapstate explain:*),Bash(tapstate desc:*)"
    cmd = [CLAUDE, "-p", "--output-format", "stream-json", "--verbose", "--model", "claude-opus-5-5",
           "--mcp-config", str(cfg), "--strict-mcp-config", "--tools", "Read,Glob,Grep,Write,Edit,Bash",
           "--allowedTools", allowed, "--permission-mode", "acceptEdits", "--setting-sources", "",
           "--max-turns", "60", "--no-session-persistence"]
    env = child_env()
    env["PATH"] = f"{bindir}:{env.get('PATH', '')}"
    t0 = time.time()
    validations, result, final_text, pending = [], {}, "", {}
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, cwd=WS, env=env)
        proc.stdin.write(PROMPT[lang] + BRIEF)
        proc.stdin.close()
        with (WS / "stream.jsonl").open("w") as log:
            for line in proc.stdout:
                log.write(line)
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if ev.get("type") == "system" and ev.get("subtype") == "init":
                    st = {s.get("name"): s.get("status") for s in ev.get("mcp_servers") or []}
                    tools = [t for t in ev.get("tools") or [] if t.startswith("mcp__tapstate")]
                    print(f"  {D}MCP tapstate: {st.get('tapstate')} · {len(tools)} tools{R}", flush=True)
                elif ev.get("type") == "assistant":
                    for b in ev["message"].get("content") or []:
                        if b.get("type") == "tool_use":
                            name, inp = b["name"], b.get("input") or {}
                            pending[b["id"]] = (name, inp)
                            if name.startswith("mcp__tapstate__"):
                                args = {k: v for k, v in inp.items() if v not in (None, "", [], {})}
                                print(f"  {M}{T['mcp']}{R}   {name.split('__', 2)[2]} {D}{short(args, 80)}{R}", flush=True)
                            elif name == "Bash":
                                print(f"  {C}$ {inp.get('command', '')}{R}", flush=True)
                            elif name in ("Write", "Edit"):
                                p = str(inp.get("file_path", "")).replace(str(WS) + "/", "")
                                print(f"  {C}{name:<5}{R} {T['writes']} {p}", flush=True)
                            else:
                                p = str(inp.get("file_path") or inp.get("pattern") or inp.get("path") or "")
                                print(f"  {C}{name:<5}{R} {T['reads']} {p.replace(str(WS) + '/', '')}", flush=True)
                        elif b.get("type") == "text" and b.get("text", "").strip():
                            final_text = b["text"].strip()
                elif ev.get("type") == "user":
                    for b in (ev.get("message") or {}).get("content") or []:
                        if not isinstance(b, dict) or b.get("type") != "tool_result":
                            continue
                        name, inp = pending.get(b.get("tool_use_id"), ("", {}))
                        content = b.get("content")
                        text = content if isinstance(content, str) else "\n".join(
                            c.get("text", "") for c in content or [] if isinstance(c, dict))
                        if name == "Bash":
                            body = "\n".join(ln for ln in text.splitlines() if "SLF4J" not in ln).strip()
                            if "validate" in inp.get("command", ""):
                                ok = body.startswith("valid") and not b.get("is_error")
                                validations.append({"command": inp.get("command"), "ok": ok, "output": body})
                                col = G if ok else RED
                            else:
                                col = D
                            for ln in body.splitlines()[:8]:
                                print(f"    {col}{ln}{R}", flush=True)
                            if len(body.splitlines()) > 8:
                                print(f"    {D}…{R}", flush=True)
                        elif name.startswith("mcp__tapstate__"):
                            print(f"    {D}↳ {len(text):,} chars{' (error)' if b.get('is_error') else ''}{R}", flush=True)
                elif ev.get("type") == "result":
                    result = ev
        proc.wait()
    finally:
        shutil.rmtree(cfgdir, ignore_errors=True)
        shutil.rmtree(bindir, ignore_errors=True)
    secs = time.time() - t0
    u = result.get("usage") or {}
    tokens = {"input": u.get("input_tokens", 0), "cache_write": u.get("cache_creation_input_tokens", 0),
              "cache_read": u.get("cache_read_input_tokens", 0), "output": u.get("output_tokens", 0)}
    total = sum(tokens.values())
    print(f"\n{B}{T['done']}{R}  " + T["usage"].format(t=total, i=tokens["input"], cw=tokens["cache_write"],
                                                      cr=tokens["cache_read"], o=tokens["output"],
                                                      c=result.get("total_cost_usd") or 0, s=secs))
    if final_text:
        print(f"{D}{final_text}{R}")
    draft = WS / "pipeline" / "product_state_ai.tap.yml"
    print(f"\n{B}$ cat pipeline/product_state_ai.tap.yml{R}")
    print(draft.read_text() if draft.exists() else "(no file written)", flush=True)
    time.sleep(1.5)
    print(f"{B}{T['check']}{R}")
    print(f"{C}$ tapstate validate .{R}", flush=True)
    rc_v, out_v = tapstate("validate", ".")
    print(out_v, flush=True)
    rc_d, out_d = (None, "")
    if draft.exists():
        print(f"{C}$ tapstate desc product_state_ai -w .{R}", flush=True)
        rc_d, out_d = tapstate("desc", "product_state_ai", "-w", ".")
        print(out_d, flush=True)
    (WS / "result.json").write_text(json.dumps({
        "prompt": PROMPT[lang], "draft": draft.read_text() if draft.exists() else None, "explanation": final_text,
        "claude_validations": validations, "validate": {"exit": rc_v, "output": out_v},
        "desc": {"exit": rc_d, "output": out_d}, "tokens": {**tokens, "total": total},
        "cost_usd": result.get("total_cost_usd"), "seconds": round(secs, 1), "num_turns": result.get("num_turns"),
        "is_error": result.get("is_error")}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
