#!/usr/bin/env python3
"""
Capture a fresh X-Line-Access token from LINE's SSL traffic and save it
to ~/.config/line-mcp/auth.json for use by the CDN download path.

Usage:
    python -u tools/refresh_token.py [--host 192.168.240.112] [--port 27042]

Then trigger LINE to make a network request (open any chat, send a message,
or tap any image). The script exits as soon as the token is captured.

FRIDA_HOST is the IP of the Waydroid container running frida-server.
Default is the standard Waydroid subnet address (192.168.240.112).
"""
from __future__ import annotations
import argparse, sys, time
from pathlib import Path

try:
    import frida_tools as _ft
    _BRIDGES_DIR = Path(_ft.__file__).parent / "bridges"
except Exception:
    import frida
    _BRIDGES_DIR = Path(frida.__file__).parent / "bridges"

import frida

parser = argparse.ArgumentParser(add_help=False)
parser.add_argument("--host", default="192.168.240.112")
parser.add_argument("--port", type=int, default=27042)
_args, _ = parser.parse_known_args()

FRIDA_HOST = _args.host
FRIDA_PORT = _args.port

_JS = r"""
'use strict';
var _captured = false;

// Hook SSL_write to intercept X-Line-Access header
var libssl = Process.findModuleByName('libssl.so.3') || Process.findModuleByName('libssl.so');
if (!libssl) {
    Process.enumerateModules().forEach(function(m) {
        if (!libssl && (m.name.indexOf('ssl') !== -1 || m.name.indexOf('SSL') !== -1)) libssl = m;
    });
}

function hookSSL(mod) {
    var writeExp = mod.findExportByName('SSL_write');
    if (!writeExp) return;
    Interceptor.attach(writeExp, {
        onEnter: function(args) {
            if (_captured) return;
            var len = args[2].toInt32();
            if (len < 100 || len > 65536) return;
            try {
                var buf = Memory.readUtf8String(args[1], len);
                var m = buf.match(/X-Line-Access:\s*([^\r\n]+)/i);
                if (m) {
                    _captured = true;
                    send({ type: 'token', token: m[1].trim() });
                }
            } catch(e) {}
        }
    });
    send({ type: 'info', msg: 'SSL_write hooked in ' + mod.name });
}

if (libssl) {
    hookSSL(libssl);
} else {
    send({ type: 'info', msg: 'libssl not found at hook time; watching module loads...' });
    Process.setExceptionHandler(function() { return false; });
}
"""


def load_bridge(script, name):
    bridge_path = _BRIDGES_DIR / (name.lower() + ".js")
    source = bridge_path.read_text()
    script.post({"type": "frida:bridge-loaded", "filename": name.lower() + ".js", "source": source})


def main():
    import subprocess as _sp
    dev = frida.get_device_manager().add_remote_device(f"{FRIDA_HOST}:{FRIDA_PORT}")
    r = _sp.run(
        ["sudo", "waydroid", "shell", "--", "sh", "-c",
         "ps -A | grep '[l]ine.android' | awk '{print $2}'"],
        capture_output=True, text=True, timeout=15,
    )
    pids = [p for p in r.stdout.strip().split() if p.isdigit()]
    if not pids:
        print("[!] LINE not running — start LINE first", file=sys.stderr)
        sys.exit(1)
    pid = int(pids[0])
    print(f"[*] Attaching to LINE pid={pid}", file=sys.stderr)

    session = dev.attach(pid)
    script = session.create_script(_JS)
    captured = [False]

    def on_message(msg, data):
        if msg.get("type") == "send":
            p = msg["payload"]
            t = p.get("type", "")
            if t == "info":
                print(f"[i] {p['msg']}", file=sys.stderr)
            elif t == "token":
                token = p["token"]
                print(f"[+] Token captured ({len(token)} chars)", file=sys.stderr)
                # Save to auth.json
                from line_db import save_auth_token
                save_auth_token(token)
                print(f"[+] Saved to ~/.config/line-mcp/auth.json", file=sys.stderr)
                captured[0] = True
        elif msg.get("type") == "error":
            print(f"[!] {msg.get('description','?')}", file=sys.stderr)

    script.on("message", on_message)
    script.load()
    print("[*] Waiting for X-Line-Access token — trigger any LINE network activity...", file=sys.stderr)

    for _ in range(120):
        time.sleep(1)
        if captured[0]:
            break

    if not captured[0]:
        print("[!] Timed out — no token captured", file=sys.stderr)
        sys.exit(1)

    session.detach()
    print("[*] Done.", file=sys.stderr)


if __name__ == "__main__":
    main()
