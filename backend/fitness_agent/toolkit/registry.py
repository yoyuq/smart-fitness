"""Tool registry for Smart Fitness Agent internal tools.

This is not an MCP server. It is the app-internal registry that powers the
Agent loop; a future MCP adapter can expose these registered tools through
MCP tools/list and tools/call without changing handlers.
"""
import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

ToolHandler = Callable[[sqlite3.Connection, int, Dict[str, Any]], Dict[str, Any]]


@dataclass(frozen=True)
class ToolDef:
    name: str
    description: str
    args: Dict[str, Any]
    handler: ToolHandler
    read_only: bool = True
    requires_approval: bool = False
    network: Optional[str] = None

    def spec(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "args": self.args,
            "read_only": self.read_only,
        }
        if self.requires_approval:
            out["requires_approval"] = True
        if self.network:
            out["network"] = self.network
        return out


_REGISTRY: Dict[str, ToolDef] = {}


def register_tool(
    *,
    name: str,
    description: str,
    args: Optional[Dict[str, Any]] = None,
    read_only: bool = True,
    requires_approval: bool = False,
    network: Optional[str] = None,
):
    def decorator(fn: ToolHandler):
        if name in _REGISTRY:
            raise RuntimeError(f"duplicate tool registered: {name}")
        _REGISTRY[name] = ToolDef(
            name=name,
            description=description,
            args=args or {},
            handler=fn,
            read_only=read_only,
            requires_approval=requires_approval,
            network=network,
        )
        return fn
    return decorator


def tool_specs() -> List[Dict[str, Any]]:
    return [tool.spec() for tool in _REGISTRY.values()]


def tool_names() -> List[str]:
    return list(_REGISTRY.keys())


def execute_registered_tool(conn: sqlite3.Connection, user_id: int, name: str, args: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    tool = _REGISTRY.get(name)
    if not tool:
        return {
            "ok": False,
            "error": f"tool not allowed: {name}",
            "error_type": "unknown_tool",
            "recoverable": True,
            "available_tools": sorted(_REGISTRY.keys()),
        }
    return tool.handler(conn, user_id, args or {})


def tool_specs_for_prompt() -> str:
    return json.dumps(tool_specs(), ensure_ascii=False, indent=2)
