#!/usr/bin/env python3
"""redroid-screen — a tiny browser viewer/controller for a headless Redroid (Android) container.

The redroid `_64only` image ships no VNC server and no H.264 encoder, so noVNC and scrcpy
cannot attach. This serves `adb exec-out screencap -p` as a refreshing image and forwards
clicks, drags, keys and text through `adb shell input`. Good enough to drive the LINE QR
login and to inspect the screen from any browser on the LAN.

    python3 tools/redroid-screen.py                 # http://<lxc-ip>:6080/
    python3 tools/redroid-screen.py --port 6080 --token s3cret --interval 800

Only the Python standard library is used. Optional --token requires `?token=...` on every
request (the page carries it along). Do not expose this beyond a trusted LAN: whoever reaches
it controls the Android screen.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

ADB_DEVICE = os.environ.get("LINE_ADB_DEVICE", "127.0.0.1:5555")
TOKEN = ""
INTERVAL_MS = 1000
_SIZE: tuple[int, int] | None = None


def adb(*args: str, binary: bool = False, timeout: int = 15) -> bytes | str:
    cmd = ["adb", "-s", ADB_DEVICE, *args]
    out = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout).stdout
    return out if binary else out.decode("utf-8", "replace")


def screen_size() -> tuple[int, int]:
    global _SIZE
    if _SIZE is None:
        out = adb("shell", "wm", "size")  # "Physical size: 720x1280"
        for tok in out.split():
            if "x" in tok and tok.replace("x", "").isdigit():
                w, h = tok.split("x")
                _SIZE = (int(w), int(h))
                break
        _SIZE = _SIZE or (720, 1280)
    return _SIZE


PAGE = """<!doctype html><meta charset=utf-8><title>redroid screen</title>
<style>
body{margin:0;background:#111;color:#ddd;font:14px system-ui;display:flex;gap:12px;flex-wrap:wrap;padding:12px}
#img{max-height:92vh;max-width:70vw;border:1px solid #444;cursor:crosshair;touch-action:none;background:#000}
.panel{min-width:220px;max-width:320px}
button{margin:2px;padding:6px 10px;background:#333;color:#eee;border:1px solid #555;border-radius:4px;cursor:pointer}
input[type=text]{width:200px;padding:5px;background:#222;color:#eee;border:1px solid #555}
#log{white-space:pre-wrap;font:12px monospace;color:#9c9;max-height:40vh;overflow:auto}
</style>
<img id=img alt="screen">
<div class=panel>
 <div><b>redroid screen</b> — click = tap, drag = swipe</div>
 <div style="margin:6px 0">
  <button onclick="key(4)">◀ Back</button><button onclick="key(3)">⌂ Home</button>
  <button onclick="key(187)">▣ Recents</button><button onclick="key(66)">⏎ Enter</button>
  <button onclick="key(67)">⌫</button><button onclick="key(26)">⏻ Power</button>
 </div>
 <div><input id=txt type=text placeholder="type text, press Enter to send">
  <button onclick="sendText()">send</button></div>
 <div style="margin:6px 0">refresh <input id=iv type=number value=__IV__ style="width:70px"> ms
  <button onclick="refresh()">now</button>
  <a id=dl href="#" download="screen.png" style="color:#8cf;margin-left:8px">save png</a></div>
 <div id=log></div>
</div>
<script>
const Q = location.search; const img = document.getElementById('img');
let dev = {w:720,h:1280}, down=null, t0=0;
function log(s){const l=document.getElementById('log'); l.textContent=(new Date().toLocaleTimeString()+' '+s+'\\n'+l.textContent).slice(0,4000);}
function refresh(){ img.src = '/screen.png'+Q+(Q?'&':'?')+'t='+Date.now(); document.getElementById('dl').href = img.src; }
function post(path, params){ const u = new URL(path, location); for (const k in params) u.searchParams.set(k, params[k]); if (Q) u.searchParams.set('token', new URLSearchParams(Q).get('token')||''); return fetch(u, {method:'POST'}).then(r=>r.text()).then(t=>log(path+' '+JSON.stringify(params)+' → '+t.trim())); }
function devXY(e){ const r = img.getBoundingClientRect(); return {x: Math.round((e.clientX-r.left)/r.width*dev.w), y: Math.round((e.clientY-r.top)/r.height*dev.h)}; }
img.addEventListener('pointerdown', e=>{ down=devXY(e); t0=Date.now(); e.preventDefault(); });
img.addEventListener('pointerup', e=>{ if(!down) return; const up=devXY(e); const dt=Date.now()-t0;
  if (Math.hypot(up.x-down.x, up.y-down.y) < 12) post('/tap',{x:up.x,y:up.y}); else post('/swipe',{x1:down.x,y1:down.y,x2:up.x,y2:up.y,ms:Math.max(100,Math.min(dt,1500))});
  down=null; setTimeout(refresh, 600); });
function key(c){ post('/key',{code:c}); setTimeout(refresh, 600); }
function sendText(){ const v=document.getElementById('txt').value; if(!v) return; post('/text',{s:v}); document.getElementById('txt').value=''; setTimeout(refresh, 800); }
document.getElementById('txt').addEventListener('keydown', e=>{ if(e.key==='Enter'){ sendText(); } });
fetch('/size'+Q).then(r=>r.json()).then(s=>{dev=s; log('device '+s.w+'x'+s.h);});
img.onload = ()=>setTimeout(refresh, +document.getElementById('iv').value||1000);
img.onerror = ()=>setTimeout(refresh, 2000);
refresh();
</script>"""


class H(BaseHTTPRequestHandler):
    server_version = "redroid-screen/1"

    def log_message(self, fmt, *args):  # quieter
        if "/screen.png" not in (args[0] if args else ""):
            sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    def _q(self):
        u = urlparse(self.path)
        return u.path, {k: v[0] for k, v in parse_qs(u.query).items()}

    def _auth(self, q) -> bool:
        if TOKEN and q.get("token") != TOKEN:
            self.send_response(403); self.end_headers(); self.wfile.write(b"forbidden"); return False
        return True

    def _send(self, code: int, body: bytes, ctype="text/plain; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path, q = self._q()
        if not self._auth(q):
            return
        if path == "/":
            self._send(200, PAGE.replace("__IV__", str(INTERVAL_MS)).encode(), "text/html; charset=utf-8")
        elif path == "/screen.png":
            png = adb("exec-out", "screencap", "-p", binary=True, timeout=20)
            if not png.startswith(b"\x89PNG"):
                self._send(503, b"screencap failed: " + png[:200])
            else:
                self._send(200, png, "image/png")
        elif path == "/size":
            w, h = screen_size()
            self._send(200, json.dumps({"w": w, "h": h}).encode(), "application/json")
        elif path == "/ui.xml":
            adb("shell", "uiautomator", "dump", "/sdcard/ui.xml", timeout=30)
            self._send(200, adb("exec-out", "cat", "/sdcard/ui.xml", binary=True), "text/xml; charset=utf-8")
        else:
            self._send(404, b"not found")

    def do_POST(self):
        path, q = self._q()
        if not self._auth(q):
            return
        try:
            if path == "/tap":
                out = adb("shell", "input", "tap", str(int(q["x"])), str(int(q["y"])))
            elif path == "/swipe":
                out = adb("shell", "input", "swipe", str(int(q["x1"])), str(int(q["y1"])),
                          str(int(q["x2"])), str(int(q["y2"])), str(int(q.get("ms", 300))))
            elif path == "/key":
                out = adb("shell", "input", "keyevent", str(int(q["code"])))
            elif path == "/text":
                # `input text` needs spaces as %s and no shell metacharacters
                s = q.get("s", "").replace(" ", "%s")
                s = "".join(c for c in s if c.isalnum() or c in "%s@._-+:/")
                out = adb("shell", "input", "text", s)
            else:
                self._send(404, b"not found"); return
            self._send(200, (out or "ok").encode())
        except (KeyError, ValueError) as e:
            self._send(400, f"bad request: {e}".encode())


def main() -> int:
    global TOKEN, INTERVAL_MS, ADB_DEVICE
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=6080)
    ap.add_argument("--token", default=os.environ.get("REDROID_SCREEN_TOKEN", ""))
    ap.add_argument("--interval", type=int, default=1000, help="default refresh interval in ms")
    ap.add_argument("--device", default=ADB_DEVICE)
    a = ap.parse_args()
    TOKEN, INTERVAL_MS, ADB_DEVICE = a.token, a.interval, a.device
    subprocess.run(["adb", "connect", ADB_DEVICE], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"redroid-screen: http://{a.host}:{a.port}/" + (f"?token={TOKEN}" if TOKEN else ""), flush=True)
    ThreadingHTTPServer((a.host, a.port), H).serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
