"""A minimal Chrome DevTools Protocol client: start headless Chrome, open a page, evaluate JavaScript,
take screenshots. Used by make_video.py; needs only aiohttp (no Playwright or ffmpeg on this host)."""
import asyncio
import base64
import itertools
import json
import os
import shutil
import subprocess
import tempfile
import time

import aiohttp

CHROME = os.environ.get("CHROME_BIN") or shutil.which("google-chrome") or shutil.which("chromium")


class Chrome:
    def __init__(self, width=1920, height=1080, port=9333, args=()):
        self.width, self.height, self.port = width, height, port
        self.args = list(args)
        self.ids = itertools.count(1)
        self.pending = {}
        self.events = []      # CDP events (e.g. Network.*), when a domain is enabled

    async def __aenter__(self):
        self.profile = tempfile.mkdtemp(prefix="cdp-")
        self.proc = subprocess.Popen(
            [CHROME, "--headless=new", f"--remote-debugging-port={self.port}", f"--user-data-dir={self.profile}",
             f"--window-size={self.width},{self.height}", "--hide-scrollbars", "--no-first-run",
             "--autoplay-policy=no-user-gesture-required", "--force-device-scale-factor=1", *self.args, "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.http = aiohttp.ClientSession()
        for _ in range(100):
            try:
                async with self.http.get(f"http://127.0.0.1:{self.port}/json") as r:
                    targets = await r.json()
                page = next(t for t in targets if t["type"] == "page")
                break
            except Exception:
                await asyncio.sleep(0.1)
        self.ws = await self.http.ws_connect(page["webSocketDebuggerUrl"], max_msg_size=0)
        self.reader = asyncio.create_task(self._read())
        await self.send("Page.enable")
        await self.send("Runtime.enable")
        await self.send("Emulation.setDeviceMetricsOverride",
                        {"width": self.width, "height": self.height, "deviceScaleFactor": 1, "mobile": False})
        return self

    async def __aexit__(self, *exc):
        self.reader.cancel()
        await self.ws.close()
        await self.http.close()
        self.proc.terminate()
        self.proc.wait()
        shutil.rmtree(self.profile, ignore_errors=True)

    async def _read(self):
        async for msg in self.ws:
            data = json.loads(msg.data)
            fut = self.pending.pop(data.get("id"), None)
            if fut and not fut.done():
                fut.set_result(data)
            elif "method" in data:
                self.events.append(data)

    async def send(self, method, params=None):
        i = next(self.ids)
        fut = asyncio.get_running_loop().create_future()
        self.pending[i] = fut
        await self.ws.send_str(json.dumps({"id": i, "method": method, "params": params or {}}))
        data = await fut
        if "error" in data:
            raise RuntimeError(f"{method}: {data['error']}")
        return data.get("result", {})

    async def goto(self, url, settle=0.5):
        await self.send("Page.navigate", {"url": url})
        for _ in range(200):
            if await self.eval("document.readyState") == "complete":
                break
            await asyncio.sleep(0.05)
        await asyncio.sleep(settle)

    async def eval(self, expr, await_promise=False):
        r = await self.send("Runtime.evaluate", {"expression": expr, "returnByValue": True,
                                                 "awaitPromise": await_promise})
        if "exceptionDetails" in r:
            raise RuntimeError(f"JS: {r['exceptionDetails']}")
        return r.get("result", {}).get("value")

    async def screenshot(self, path, full_page=False):
        params = {"format": "png"}
        if full_page:
            h = await self.eval("document.documentElement.scrollHeight")
            params.update(captureBeyondViewport=True,
                          clip={"x": 0, "y": 0, "width": self.width, "height": h, "scale": 1})
        r = await self.send("Page.captureScreenshot", params)
        with open(path, "wb") as f:
            f.write(base64.b64decode(r["data"]))


async def wait_until(chrome, expr, timeout=3600, every=1.0):
    start = time.time()
    while time.time() - start < timeout:
        if await chrome.eval(expr):
            return
        await asyncio.sleep(every)
    raise TimeoutError(expr)
