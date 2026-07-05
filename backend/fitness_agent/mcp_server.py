"""Stdio MCP server exposing Smart Fitness internal Agent tools.

Usage example:
  set SMART_FITNESS_MCP_DB=C:\\Users\\hjl\\Projects\\smart_fitness\\backend\\fitness.db
  set SMART_FITNESS_MCP_USER_ID=31
  python -m fitness_agent.mcp_server

This is intentionally stdio-only and user-scoped. Write tools still execute as
raw tools here, so only run this server in trusted local MCP clients.
"""
import contextlib
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, Optional

# MCP stdio requires stdout to contain only JSON-RPC frames. Some imported
# dependencies may print warnings during import, so send import-time chatter to
# stderr and restore stdout before the server loop starts.
_REAL_STDOUT = sys.stdout
with contextlib.redirect_stdout(sys.stderr):
    from .mcp_client import MCP_PROTOCOL_VERSION, internal_spec_to_mcp_tool
    # Register built-in tools without exposing configured external MCP servers
    # back through this server.
    from .toolkit import context as _context  # noqa: F401
    from .toolkit import workouts as _workouts  # noqa: F401
    from .toolkit import plans as _plans  # noqa: F401
    from .toolkit import memory_tools as _memory_tools  # noqa: F401
    from .toolkit import knowledge_tools as _knowledge_tools  # noqa: F401
    from .toolkit import web_tools as _web_tools  # noqa: F401
    from .toolkit import todo_tools as _todo_tools  # noqa: F401
    from .toolkit.registry import execute_registered_tool, tool_specs as internal_tool_specs
sys.stdout = _REAL_STDOUT


def _default_db_path() -> str:
    return str(Path(__file__).resolve().parents[1] / "fitness.db")


def _open_conn() -> sqlite3.Connection:
    db_path = os.environ.get("SMART_FITNESS_MCP_DB") or _default_db_path()
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _user_id() -> int:
    raw = os.environ.get("SMART_FITNESS_MCP_USER_ID") or os.environ.get("AI_AGENT_MCP_USER_ID") or "1"
    return int(raw)


def _response(req_id: Any, result: Optional[Dict[str, Any]] = None, error: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    out = {"jsonrpc": "2.0", "id": req_id}
    if error is not None:
        out["error"] = error
    else:
        out["result"] = result or {}
    return out


def _content_text(value: Any) -> Dict[str, Any]:
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, default=str)
    return {"type": "text", "text": text}


def _handle(method: str, params: Dict[str, Any]) -> Dict[str, Any]:
    if method == "initialize":
        return {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "smart-fitness-agent-tools", "version": "0.1"},
        }
    if method == "tools/list":
        return {"tools": [internal_spec_to_mcp_tool(spec) for spec in internal_tool_specs()]}
    if method == "tools/call":
        name = (params.get("name") or "").strip()
        args = params.get("arguments") or {}
        if not isinstance(args, dict):
            args = {}
        conn = _open_conn()
        try:
            result = execute_registered_tool(conn, _user_id(), name, args)
        finally:
            conn.close()
        return {"content": [_content_text(result)], "structuredContent": result, "isError": not bool(result.get("ok", True))}
    # Notifications are handled by caller before this function.
    raise ValueError(f"unsupported method: {method}")


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        msg = None
        try:
            msg = json.loads(line)
            method = msg.get("method") or ""
            # JSON-RPC notification: no response.
            if "id" not in msg:
                continue
            result = _handle(method, msg.get("params") or {})
            print(json.dumps(_response(msg.get("id"), result=result), ensure_ascii=False), flush=True)
        except Exception as exc:
            req_id = msg.get("id") if isinstance(msg, dict) else None
            print(json.dumps(_response(req_id, error={"code": -32000, "message": str(exc)[:500]}), ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
