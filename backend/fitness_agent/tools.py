"""Compatibility shim for Smart Fitness Agent tools.

The tool implementation now lives in `fitness_agent.toolkit`, where each tool
category registers handlers with a shared registry. Existing imports keep
working:

    from fitness_agent.tools import TOOL_SPECS, execute_tool, tool_specs_for_prompt

This internal tool registry is not MCP by itself. It can be exposed through an
MCP adapter later without changing the registered handlers.
"""
from .toolkit import TOOL_SPECS, execute_tool, tool_specs_for_prompt

__all__ = ["TOOL_SPECS", "execute_tool", "tool_specs_for_prompt"]
