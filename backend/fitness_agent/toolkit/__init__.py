"""Modular Smart Fitness Agent toolkit.

Public surface kept compatible with `fitness_agent.tools`:
- TOOL_SPECS
- execute_tool(conn, user_id, name, args)
- tool_specs_for_prompt()

This package now includes a minimal MCP compatibility layer: internal tools are
kept in this registry, and configured external MCP stdio server tools are
surfaced with names like `mcp__server__tool`.
"""
from .registry import execute_registered_tool, tool_specs as _internal_tool_specs
from ..mcp_client import call_mcp_tool, is_mcp_tool, list_mcp_tool_specs

# Import modules for registration side effects.
from . import context as _context  # noqa: F401
from . import workouts as _workouts  # noqa: F401
from . import reps as _reps  # noqa: F401
from . import plans as _plans  # noqa: F401
from . import memory_tools as _memory_tools  # noqa: F401
from . import knowledge_tools as _knowledge_tools  # noqa: F401
from . import web_tools as _web_tools  # noqa: F401
from . import vision_tools as _vision_tools  # noqa: F401
from . import todo_tools as _todo_tools  # noqa: F401

def tool_specs():
    return _internal_tool_specs() + list_mcp_tool_specs()


TOOL_SPECS = _internal_tool_specs()
_ALLOWED = {t["name"] for t in TOOL_SPECS}


def execute_tool(conn, user_id: int, name: str, args=None):
    if is_mcp_tool(name):
        return call_mcp_tool(name, args or {})
    return execute_registered_tool(conn, user_id, name, args)


def tool_specs_for_prompt() -> str:
    import json
    return json.dumps(tool_specs(), ensure_ascii=False, indent=2)


__all__ = ["TOOL_SPECS", "execute_tool", "tool_specs_for_prompt"]
