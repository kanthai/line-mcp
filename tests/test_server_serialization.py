import importlib.util
import json
import sys
import threading
import time
import types
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch


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


def test_summarize_recent_activity_defaults_to_richer_context(monkeypatch):
    monkeypatch.setenv("LINE_MCP_MAX_CONCURRENCY", "2")
    server = _load_server_module()

    with patch.object(server, "summarize_recent_activity") as summarize:
        summarize.return_value = {
            "since_ms": 1,
            "hours": 24,
            "chat_count": 0,
            "activities": [],
            "messages_by_chat": {},
        }

        result = server.summarize_recent_activity_tool()

    assert result["messages_by_chat"] == {}
    summarize.assert_called_once_with(hours=24, chat_limit=30, messages_per_chat=24)


def test_to_dicts_marks_outgoing_message_direction(monkeypatch):
    monkeypatch.setenv("LINE_MCP_MAX_CONCURRENCY", "2")
    server = _load_server_module()

    @dataclass
    class FakeMessage:
        message_id: int
        sender_id: str
        sender_name: str
        text: str

    rows = server._to_dicts([FakeMessage(1, "", "", "hello")])

    assert rows == [{
        "message_id": 1,
        "sender_id": "",
        "sender_name": "You",
        "text": "hello",
        "direction": "outgoing",
    }]


def test_to_dicts_marks_inbound_message_direction(monkeypatch):
    monkeypatch.setenv("LINE_MCP_MAX_CONCURRENCY", "2")
    server = _load_server_module()

    @dataclass
    class FakeMessage:
        message_id: int
        sender_id: str
        sender_name: str
        text: str

    rows = server._to_dicts([FakeMessage(1, "u123", "Alice", "hello")])

    assert rows[0]["sender_name"] == "Alice"
    assert rows[0]["direction"] == "inbound"


def test_summarize_recent_activity_marks_latest_outgoing_direction(monkeypatch):
    monkeypatch.setenv("LINE_MCP_MAX_CONCURRENCY", "2")
    server = _load_server_module()

    @dataclass
    class FakeActivity:
        chat_id: str
        latest_sender_id: str
        latest_sender_name: str
        latest_text: str

    with patch.object(server, "summarize_recent_activity") as summarize:
        summarize.return_value = {
            "since_ms": 1,
            "hours": 24,
            "chat_count": 1,
            "activities": [FakeActivity("u1", "", "", "outgoing text")],
            "messages_by_chat": {},
        }

        result = server.summarize_recent_activity_tool()

    activity = result["activities"][0]
    assert activity["latest_direction"] == "outgoing"
    assert activity["latest_sender_name"] == "You"


def test_summarize_recent_activity_returns_source_constraints(monkeypatch):
    monkeypatch.setenv("LINE_MCP_MAX_CONCURRENCY", "2")
    server = _load_server_module()

    @dataclass
    class FakeActivity:
        chat_id: str
        chat_name: str
        latest_sender_id: str
        latest_sender_name: str
        latest_text: str

    with patch.object(server, "summarize_recent_activity") as summarize:
        summarize.return_value = {
            "since_ms": 1,
            "hours": 48,
            "chat_count": 1,
            "activities": [FakeActivity("u1", "Source Chat", "u2", "Alice", "hello")],
            "messages_by_chat": {},
        }

        result = server.summarize_recent_activity_tool(hours=48)

    assert result["source_chat_names"] == ["Source Chat"]
    assert "Only summarize chats and messages present" in result["summary_rules"][0]
    assert result["source_limits"]["hours"] == 48
    assert result["source_limits"]["messages_per_chat"] == 24


def test_summarize_recent_activity_response_is_bounded_for_hermes(monkeypatch):
    monkeypatch.setenv("LINE_MCP_MAX_CONCURRENCY", "2")
    server = _load_server_module()

    @dataclass
    class FakeActivity:
        chat_id: str
        chat_name: str
        latest_sender_id: str
        latest_sender_name: str
        latest_text: str

    @dataclass
    class FakeMessage:
        message_id: int
        chat_id: str
        sender_id: str
        sender_name: str
        text: str
        created_at: int
        type: int

    long_text = "x" * 1000
    activities = [
        FakeActivity(f"c{chat}", f"Chat {chat}", "u1", "Alice", long_text)
        for chat in range(30)
    ]
    messages_by_chat = {
        activity.chat_id: [
            FakeMessage(i, activity.chat_id, "u1", "Alice", long_text, i, 0)
            for i in range(24)
        ]
        for activity in activities
    }

    with patch.object(server, "summarize_recent_activity") as summarize:
        summarize.return_value = {
            "since_ms": 1,
            "hours": 48,
            "chat_count": 30,
            "activities": activities,
            "messages_by_chat": messages_by_chat,
        }

        result = server.summarize_recent_activity_tool(hours=48)

    encoded = json.dumps(result, ensure_ascii=False)
    assert len(encoded) < 60_000
    assert result["source_limits"]["response_compacted"] is True
    assert result["messages_by_chat"]["c0"][0]["text"].endswith("...")
    assert len(result["messages_by_chat"]["c0"]) <= 8
