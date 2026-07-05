import json
import os
import sys

from fitness_agent.mcp_client import (
    call_mcp_tool,
    clear_mcp_cache,
    internal_spec_to_mcp_tool,
    list_mcp_tool_specs,
    mcp_permission_decision,
    mcp_tool_name,
    parse_mcp_tool_name,
)
from fitness_agent.permissions import check_permission
from fitness_agent.tools import execute_tool, tool_specs_for_prompt


def _write_fake_server(path):
    path.write_text(
        r'''
import json
import sys

TOOLS = [
    {
        "name": "echo",
        "description": "Echo a message",
        "inputSchema": {
            "type": "object",
            "properties": {"message": {"type": "string", "description": "message to echo"}},
            "required": ["message"],
        },
    },
    {
        "name": "write_note",
        "description": "pretend to write a note",
        "inputSchema": {"type": "object", "properties": {"note": {"type": "string"}}},
    },
]

for line in sys.stdin:
    if not line.strip():
        continue
    msg = json.loads(line)
    if "id" not in msg:
        continue
    method = msg.get("method")
    params = msg.get("params") or {}
    if method == "initialize":
        result = {"protocolVersion": params.get("protocolVersion"), "capabilities": {"tools": {}}, "serverInfo": {"name": "fake", "version": "1"}}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        result = {"content": [{"type": "text", "text": json.dumps({"name": name, "args": args}, ensure_ascii=False)}], "structuredContent": {"name": name, "args": args}, "isError": False}
    else:
        print(json.dumps({"jsonrpc":"2.0","id":msg.get("id"),"error":{"code":-32601,"message":"not found"}}), flush=True)
        continue
    print(json.dumps({"jsonrpc":"2.0","id":msg.get("id"),"result":result}, ensure_ascii=False), flush=True)
''',
        encoding="utf-8",
    )


def test_mcp_tool_name_roundtrip():
    name = mcp_tool_name("demo", "echo")
    assert name == "mcp__demo__echo"
    assert parse_mcp_tool_name(name) == ("demo", "echo")


def test_internal_spec_can_be_converted_to_mcp_schema():
    spec = {"name": "x", "description": "desc", "args": {"limit": "int optional", "items": "list optional"}}
    mcp = internal_spec_to_mcp_tool(spec)
    assert mcp["name"] == "x"
    assert mcp["inputSchema"]["type"] == "object"
    assert mcp["inputSchema"]["properties"]["limit"]["type"] == "integer"
    assert mcp["inputSchema"]["properties"]["items"]["type"] == "array"


def test_external_mcp_server_tools_are_listed_and_callable(monkeypatch, tmp_path):
    server = tmp_path / "fake_mcp_server.py"
    _write_fake_server(server)
    monkeypatch.setenv("AI_AGENT_MCP_SERVERS_JSON", json.dumps({
        "servers": {
            "fake": {
                "command": sys.executable,
                "args": [str(server)],
                "read_only_tools": ["echo"],
                "timeout": 5,
            }
        }
    }))
    clear_mcp_cache()

    specs = list_mcp_tool_specs(force=True)
    names = {s["name"] for s in specs}
    assert "mcp__fake__echo" in names
    assert "mcp__fake__write_note" in names
    echo_spec = next(s for s in specs if s["name"] == "mcp__fake__echo")
    write_spec = next(s for s in specs if s["name"] == "mcp__fake__write_note")
    assert echo_spec["read_only"] is True
    assert write_spec["requires_approval"] is True

    result = call_mcp_tool("mcp__fake__echo", {"message": "hi"})
    assert result["ok"] is True
    assert result["structuredContent"] == {"name": "echo", "args": {"message": "hi"}}


def test_mcp_permission_default_ask_and_read_only_allow(monkeypatch, tmp_path):
    server = tmp_path / "fake_mcp_server.py"
    _write_fake_server(server)
    monkeypatch.setenv("AI_AGENT_MCP_SERVERS_JSON", json.dumps([
        {"name": "fake", "command": sys.executable, "args": [str(server)], "read_only_tools": ["echo"], "timeout": 5}
    ]))
    clear_mcp_cache()

    assert mcp_permission_decision("mcp__fake__echo")["behavior"] == "allow"
    assert check_permission("mcp__fake__write_note")["behavior"] == "ask"
    assert check_permission("mcp__missing__echo")["behavior"] == "deny"


def test_agent_prompt_and_execute_tool_include_configured_mcp(monkeypatch, tmp_path):
    server = tmp_path / "fake_mcp_server.py"
    _write_fake_server(server)
    monkeypatch.setenv("AI_AGENT_MCP_SERVERS_JSON", json.dumps([
        {"name": "fake", "command": sys.executable, "args": [str(server)], "read_only": True, "timeout": 5}
    ]))
    clear_mcp_cache()

    prompt_specs = json.loads(tool_specs_for_prompt())
    assert "mcp__fake__echo" in {s["name"] for s in prompt_specs}
    result = execute_tool(None, 1, "mcp__fake__echo", {"message": "hello"})
    assert result["ok"] is True
    assert result["structuredContent"]["args"] == {"message": "hello"}
