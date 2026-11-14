"""Demo "three ways" beat, the AI path, run for real: Claude Code (on the Claude Code login on this
machine) drafts a Tapstate pipeline from one short prompt, using only the files prepare_ai_ws.py
exported (source definitions, Tapstate's discovered schemas, the CLI's grammar pages). Its tools are
Read, Glob, Grep and Write in recording/ai_ws/: no shell, no MCP, no connection to Tapstate. Then the
human check: `tapstate validate` of the draft against the workspace's sources, and `tapstate desc`.
Nothing is applied to the server.

Prints the work live (for record_live-style capture), and writes ai_ws/result.json: the draft, the
validation output, tokens and cost.

Usage (from demo/, env.sh sourced, TAPSTATE_CLI set): python recording/ai_draft.py [zh|en]
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
WS = HERE / "ai_ws"
sys.path.insert(0, str(HERE.parents[3] / "harness"))
from run_claude_arms import CLAUDE, child_env  # noqa: E402

PROMPT = {
    "zh": "把产品、价格、库存和供应商嵌套成一个产品文档。",
    "en": "Nest products, prices, stock and vendors into one product document.",
}
BRIEF = ("\n\nWrite this as a Tapstate 0.7.0 pipeline. Use only the files in this directory: source/ (the "
         "source definitions), schema/ (the schemas Tapstate discovered for each source) and grammar/ (the "
         "pipeline grammar, as the CLI documents it). Use pipeline id product_state_ai and write it to "
         "pipeline/product_state_ai.tap.yml. Then explain your design in two sentences.")
TEXT = {
    "zh": {"title": "AI 辅助：Claude Code 根据一句话起草管道", "prompt": "提示词", "reads": "读取",
           "writes": "写入", "done": "Claude 完成", "check": "人工确认：用 Tapstate CLI 校验草稿（不发布到服务器）",
           "usage": "token {t:,}（输入 {i}，缓存写入 {cw:,}，缓存读取 {cr:,}，输出 {o:,}） · 成本 ${c:.2f} · 用时 {s:.0f} 秒",
           "files": "只给 Claude 文件：source/ 数据源定义、schema/ Tapstate 发现的表结构、grammar/ 管道语法（不连接 Tapstate）"},
    "en": {"title": "AI-assisted: Claude Code drafts the pipeline from one sentence", "prompt": "prompt", "reads": "reads",
           "writes": "writes", "done": "Claude done", "check": "Human check: validate the draft with the Tapstate CLI (nothing applied)",
           "usage": "tokens {t:,} (input {i}, cache write {cw:,}, cache read {cr:,}, output {o:,}) · cost ${c:.2f} · {s:.0f} s",
           "files": "Claude gets files only: source/ definitions, schema/ Tapstate's discovered schemas, grammar/ (no connection to Tapstate)"},
}
B, C, D, G, Y, R = "\033[1m", "\033[36m", "\033[2m", "\033[32m", "\033[33m", "\033[0m"


def tapstate(*args):
    r = subprocess.run([os.environ.get("TAPSTATE_CLI", "tapstate"), *args], capture_output=True, text=True, cwd=WS)
    return r.returncode, "\n".join(ln for ln in (r.stdout + r.stderr).splitlines() if "SLF4J" not in ln)


def main():
    lang = sys.argv[1] if len(sys.argv) > 1 else "zh"
    T = TEXT[lang]
    print(f"{B}=== {T['title']} ==={R}")
    print(f"{D}{T['files']}{R}\n")
    print(f"{T['prompt']}: {Y}{PROMPT[lang]}{R}\n", flush=True)
    time.sleep(1)
    cmd = [CLAUDE, "-p", "--output-format", "stream-json", "--verbose", "--model", "claude-opus-5-5",
           "--tools", "Read,Glob,Grep,Write", "--allowedTools", "Read,Glob,Grep,Write",
           "--permission-mode", "acceptEdits", "--strict-mcp-config", "--setting-sources", "",
           "--max-turns", "40", "--no-session-persistence"]
    t0 = time.time()
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            cwd=WS, env=child_env())
    proc.stdin.write(PROMPT[lang] + BRIEF)
    proc.stdin.close()
    result, final_text = {}, ""
    with (WS / "stream.jsonl").open("w") as log:
        for line in proc.stdout:
            log.write(line)
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("type") == "assistant":
                for b in ev["message"].get("content") or []:
                    if b.get("type") == "tool_use":
                        inp = b.get("input") or {}
                        target = inp.get("file_path") or inp.get("pattern") or inp.get("path") or ""
                        target = str(target).replace(str(WS) + "/", "")
                        verb = T["writes"] if b["name"] == "Write" else T["reads"]
                        print(f"  {C}{b['name']:<5}{R} {verb} {target}", flush=True)
                    elif b.get("type") == "text" and b.get("text", "").strip():
                        final_text = b["text"].strip()
            elif ev.get("type") == "result":
                result = ev
    proc.wait()
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
        "validate": {"exit": rc_v, "output": out_v}, "desc": {"exit": rc_d, "output": out_d},
        "tokens": {**tokens, "total": total}, "cost_usd": result.get("total_cost_usd"), "seconds": round(secs, 1),
        "num_turns": result.get("num_turns"), "is_error": result.get("is_error")}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
