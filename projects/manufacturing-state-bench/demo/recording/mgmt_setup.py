"""Tapstate 0.8.0 first-time setup for the demo's 0.8 stack, through its management page (reached on
this machine's 127.0.0.1:44223 via the loopback relay; see stack/docker-compose-0.8.yml).

The one-time installation credential is read inside the server container and typed into the page; it is
never printed, logged or written anywhere else.

Steps (one per run, so each can be checked):
  inspect      open the page, print its headings, labels, inputs and buttons (no input), screenshot
  credential   enter the installation credential and continue
  storage      MongoDB, base URI and three database names; test all three, then save
  admin        after restart and `installation --authorize`: create the first administrator

Usage (from demo/): python recording/mgmt_setup.py <step>
"""
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

from cdp import Chrome

HERE = Path(__file__).resolve().parent
URL = os.environ.get("MGMT_URL", "http://127.0.0.1:44223")   # stage.json's managementUrl (it changes on restart)
CONTAINER = "mfg-bench08-server-1"
CAPABILITY = "/var/lib/tapstate/inst/installation/capability.json"
BASE_URI = "mongodb://mongo:27017/?directConnection=true"
DATABASES = ["tapstate", "tapstate_nest", "views"]   # configuration, cache, default target (as in 0.7)
ADMIN = ("admin", "admin")
SHOTS = HERE / "ui_probe"

DESCRIBE = """JSON.stringify({
  url: location.href,
  headings: [...document.querySelectorAll('h1,h2,h3,legend,label')].map(e => e.textContent.trim()).filter(Boolean).slice(0, 40),
  inputs: [...document.querySelectorAll('input,select,textarea')].map(e => ({tag: e.tagName, type: e.type, name: e.name,
      id: e.id, placeholder: e.placeholder, label: (e.labels && e.labels[0] ? e.labels[0].textContent.trim() : ''),
      filled: !!e.value})),
  buttons: [...document.querySelectorAll('button')].map(b => [b.textContent.trim(), b.disabled]).filter(b => b[0]),
  text: document.body.innerText.slice(0, 1500)})"""

SET = """(el, v) => { const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, v); el.dispatchEvent(new Event('input', {bubbles: true}));
  el.dispatchEvent(new Event('change', {bubbles: true})); }"""


def credential():
    """The current installation credential, read inside the container; never printed."""
    raw = subprocess.run(["docker", "exec", CONTAINER, "cat", CAPABILITY], capture_output=True, text=True,
                         check=True).stdout
    data = json.loads(raw)
    for key in ("token", "credential", "capability", "value", "secret"):
        if isinstance(data.get(key), str) and data[key]:
            return data[key]
    raise SystemExit(f"no credential field in capability.json (fields: {sorted(data)})")


async def describe(c, name):
    d = json.loads(await c.eval(DESCRIBE))
    await c.screenshot(str(SHOTS / f"mgmt_{name}.png"))
    print(json.dumps({k: d[k] for k in ("url", "headings", "inputs", "buttons")}, ensure_ascii=False, indent=1))
    print("--- page text ---\n" + d["text"])


async def click(c, pattern):
    ok = await c.eval(f"""(() => {{ const b = [...document.querySelectorAll('button')].find(b => /{pattern}/i.test(b.textContent.trim()) && !b.disabled);
      if (!b) return false; b.click(); return true; }})()""")
    if not ok:
        raise SystemExit(f"no enabled button matching /{pattern}/")
    await asyncio.sleep(2)


async def enter_credential(c):
    secret = credential()
    found = await c.eval("""(() => { const i = [...document.querySelectorAll('input')].find(e => e.type === 'password' ||
        /credential|token|凭据|凭证/i.test((e.labels && e.labels[0] ? e.labels[0].textContent : '') + e.placeholder + e.name + e.id));
      if (!i) return false; i.setAttribute('data-cred', '1'); return true; })()""")
    if not found:
        raise SystemExit("no credential input on the page")
    await c.eval(f"({SET})(document.querySelector('[data-cred]'), {json.dumps(secret)})")
    del secret
    await asyncio.sleep(0.5)
    await click(c, "continue|submit|verify|next|enter|unlock|sign in|继续|提交|验证")


async def fill_uri(c):
    await c.eval(f"({SET})([...document.querySelectorAll('input')].find(e => e.labels && e.labels[0] && "
                 f"/Connection URI|连接 URI/.test(e.labels[0].textContent)), {json.dumps(BASE_URI)})")
    await asyncio.sleep(0.5)


async def main(step):
    SHOTS.mkdir(exist_ok=True)
    async with Chrome(width=1600, height=1000, port=9346) as c:
        await c.send("Network.enable")
        await c.goto(URL, settle=3)
        if step == "inspect":
            await describe(c, "inspect")
        elif step == "customize":   # fill the URI, open Customize, list the fields; nothing saved
            await fill_uri(c)
            await click(c, "^Customize$|自定义")
            await describe(c, "customize")
        elif step == "storage":
            await fill_uri(c)
            await click(c, "^Customize$|自定义")
            for label, name in zip(("configDatabase|Config", "Cache database|缓存", "Views database|视图"), DATABASES):
                await c.eval(f"({SET})([...document.querySelectorAll('input')].find(e => e.labels && e.labels[0] && "
                             f"/{label}/.test(e.labels[0].textContent)), {json.dumps(name)})")
            await asyncio.sleep(0.5)
            # the documented order: test every layer, then save (Connect alone tested once and was refused)
            await c.eval("[...document.querySelectorAll('button')].filter(b => /^Advanced settings$|^高级设置$/.test(b.textContent.trim())).forEach(b => b.click())")
            await asyncio.sleep(1)
            tests = await c.eval("(() => { const bs = [...document.querySelectorAll('button')].filter(b => /^Test connection$|^测试连接$/.test(b.textContent.trim()) && !b.disabled); bs.forEach(b => b.click()); return bs.length; })()")
            print(f"clicked {tests} Test connection button(s)")
            await asyncio.sleep(8)
            await click(c, "^Save storage settings$|^保存存储设置$")
            await asyncio.sleep(3)
            if await c.eval("!![...document.querySelectorAll('input')].find(e => /credential|凭据|凭证|token/i.test("
                            "(e.labels && e.labels[0] ? e.labels[0].textContent : '') + e.placeholder))"):
                print("the page asks for the installation credential: entering it (not shown)")
                await enter_credential(c)
                await asyncio.sleep(3)
            await describe(c, "after_storage")
            print("--- requests (method, path, status) ---")
            reqs = {e["params"]["requestId"]: e["params"]["request"] for e in c.events
                    if e.get("method") == "Network.requestWillBeSent"}
            for e in c.events:
                if e.get("method") == "Network.responseReceived" and "/assets/" not in e["params"]["response"]["url"]:
                    r = reqs.get(e["params"]["requestId"], {})
                    print(r.get("method"), e["params"]["response"]["url"].replace(URL, ""), e["params"]["response"]["status"])
                    if e["params"]["response"]["status"] >= 500 and "/api/storage/" in e["params"]["response"]["url"]:
                        body = await c.send("Network.getResponseBody", {"requestId": e["params"]["requestId"]})
                        print("   body:", body.get("body", "")[:600])
        elif step == "details":   # the current state, with technical details expanded; nothing submitted
            await c.eval("[...document.querySelectorAll('button,summary,[role=button]')].filter(b => /Technical details|技术详情/"
                         ".test(b.textContent)).forEach(b => b.click())")
            await asyncio.sleep(1)
            await describe(c, "details")
        elif step == "admin":   # the first administrator, on the management channel
            user, password = ADMIN
            await c.eval(f"({SET})([...document.querySelectorAll('input')].find(e => e.labels && e.labels[0] && "
                         f"/^(Username|用户名)$/.test(e.labels[0].textContent.trim())), {json.dumps(user)})")
            await c.eval(f"({SET})([...document.querySelectorAll('input')].find(e => e.type === 'password'), {json.dumps(password)})")
            for extra in await c.eval("JSON.stringify([...document.querySelectorAll('input[type=password]')].length)"), :
                pass
            await c.eval(f"[...document.querySelectorAll('input[type=password]')].forEach(e => ({SET})(e, {json.dumps(password)}))")
            await asyncio.sleep(0.5)
            await click(c, "^Create administrator$|^创建管理员$")
            await asyncio.sleep(3)
            await describe(c, "after_admin")
        elif step == "credential":
            await enter_credential(c)
            await describe(c, "after_credential")
        else:
            raise SystemExit(f"unknown step {step!r}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "inspect"))
