"""Record Tapstate's web UI (the 0.7.0 server's own app) for the demo: sign in, open Pipelines and then
mfg_products in the graph editor, in 简体中文. The app starts in English on every page load and keeps
its language choice only in memory, and signing in reloads it, so the language is chosen again from the
header's language menu after signing in and the rest of the visit navigates by clicking links. Chinese text needs a CJK font on the
machine: the Noto Sans SC file from recording/fonts/ in ~/.local/share/fonts. Chrome's screencast frames
are saved with their timestamps (ui_<lang>/frame_*.jpg, ui_<lang>/frames.json) for the player.

Usage: python recording/ui_capture.py [zh|en]
"""
import asyncio
import base64
import json
import sys
import time
from pathlib import Path

from cdp import Chrome

HERE = Path(__file__).resolve().parent
URL = "http://127.0.0.1:8080"
LANG_BUTTON = {"zh": "简", "en": "EN"}
SET = """(el, v) => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(el, v);
  el.dispatchEvent(new Event('input', {bubbles: true})); }"""


async def main(lang):
    out = HERE / f"ui_{lang}"
    out.mkdir(exist_ok=True)
    for f in out.glob("frame_*.jpg"):
        f.unlink()
    frames, t0 = [], [None]
    locale = {"zh": "zh-CN", "en": "en-US"}[lang]
    async with Chrome(width=1920, height=1008, port=9342, args=[f"--lang={locale}"]) as c:
        # the app picks its language from the browser; its own switch is lost on navigation
        await c.send("Emulation.setLocaleOverride", {"locale": locale})
        ua = await c.eval("navigator.userAgent")
        await c.send("Network.setUserAgentOverride", {"userAgent": ua, "acceptLanguage": f"{locale},{locale[:2]}"})
        await c.send("Page.addScriptToEvaluateOnNewDocument", {"source":
            f"Object.defineProperty(navigator, 'language', {{get: () => '{locale}'}});"
            f"Object.defineProperty(navigator, 'languages', {{get: () => ['{locale}', '{locale[:2]}']}});"})
        await c.goto(URL + "/", settle=2)
        await c.eval(f"[...document.querySelectorAll('button')].find(b => b.textContent.trim() === '{LANG_BUTTON[lang]}').click()")
        await asyncio.sleep(1)

        async def pump():   # screencast frames arrive as events; ack each one
            seen = 0
            while True:
                for ev in c.events[seen:]:
                    if ev.get("method") == "Page.screencastFrame":
                        p = ev["params"]
                        t = p["metadata"]["timestamp"]
                        t0[0] = t0[0] or t
                        name = f"frame_{len(frames):04d}.jpg"
                        (out / name).write_bytes(base64.b64decode(p["data"]))
                        frames.append([round(t - t0[0], 3), name])
                        await c.send("Page.screencastFrameAck", {"sessionId": p["sessionId"]})
                seen = len(c.events)
                await asyncio.sleep(0.03)
        await c.send("Page.startScreencast", {"format": "jpeg", "quality": 85, "maxWidth": 1920, "maxHeight": 1008,
                                              "everyNthFrame": 1})
        task = asyncio.create_task(pump())
        await asyncio.sleep(1.5)
        ins = "[...document.querySelectorAll('input')]"
        await c.eval(f"({SET})({ins}.find(i => i.type !== 'password'), 'admin')")
        await asyncio.sleep(0.6)
        await c.eval(f"({SET})({ins}.find(i => i.type === 'password'), 'admin')")
        await asyncio.sleep(0.6)
        await c.eval("[...document.querySelectorAll('button')].find(b => b.type === 'submit' || /Sign in|登录|登入/.test(b.textContent)).click()")
        await asyncio.sleep(3.5)
        # signing in reloads the app in English; choose the language again from the user menu
        # the header's language menu: the first menu button; items are English, 简体, 繁體 in that order
        await c.eval("document.querySelector('header button[aria-haspopup=\"menu\"], button[aria-haspopup=\"menu\"]').click()")
        await asyncio.sleep(1)
        index = {"en": 0, "zh": 1}[lang]
        await c.eval(f"document.querySelectorAll('[role=menuitem]')[{index}].click()")
        await asyncio.sleep(0.8)
        await c.eval("document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}))")
        await asyncio.sleep(1.5)
        await c.eval("document.querySelector('a[href=\"/pipelines\"]').click()")
        await asyncio.sleep(3)
        await c.eval("document.querySelector('a[href=\"/pipelines/mfg_products/edit\"]').click()")
        await asyncio.sleep(6)
        await c.send("Page.stopScreencast")
        await asyncio.sleep(0.5)
        task.cancel()
        text = await c.eval("document.body.innerText")
    (out / "frames.json").write_text(json.dumps(frames))
    print(f"{len(frames)} frames over {frames[-1][0] if frames else 0:.1f} s into {out}; page text: {text[:120]!r}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "zh"))
