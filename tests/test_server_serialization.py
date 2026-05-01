import importlib.util
import sys
import threading
import time
import types
from pathlib import Path


def _load_server_module():
    server_path = Path(__file__).resolve().parents[1] / "mcp" / "server.py"
    mcp_module = types.ModuleType("mcp")
    server_module = types.ModuleType("mcp.server")

    class FakeFastMCP:
        def __init__(self, *args, **kwargs):
            pass

        def tool(self, *args, **kwargs):
            def decorate(func):
                return func

            return decorate

    server_module.FastMCP = FakeFastMCP
    sys.modules["mcp"] = mcp_module
    sys.modules["mcp.server"] = server_module

    spec = importlib.util.spec_from_file_location("line_mcp_server_for_test", server_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_serialized_line_call_respects_configured_concurrency(monkeypatch):
    monkeypatch.setenv("LINE_MCP_MAX_CONCURRENCY", "2")
    server = _load_server_module()
    events = []
    active = 0
    max_active = 0
    active_lock = threading.Lock()

    @server._serialized_line_call
    def slow_call(name):
        nonlocal active, max_active
        with active_lock:
            active += 1
            max_active = max(max_active, active)
        events.append(("start", name, time.monotonic()))
        time.sleep(0.05)
        events.append(("end", name, time.monotonic()))
        with active_lock:
            active -= 1

    threads = [threading.Thread(target=slow_call, args=(str(i),)) for i in range(4)]

    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    starts = [event for event in events if event[0] == "start"]

    assert len(starts) == 4
    assert max_active == 2
