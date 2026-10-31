"""Register the built-in tools and preserve the public tools facade."""
from . import (context, knowledge_tools, memory_tools, plans, reps, todo_tools,
               vision_tools, web_tools, workouts)
from .registry import execute_registered_tool as execute_tool
from .registry import tool_specs, tool_specs_for_prompt

TOOL_SPECS = tool_specs()
__all__ = ["TOOL_SPECS", "execute_tool", "tool_specs_for_prompt"]
