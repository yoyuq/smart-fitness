"""Fitness Agent toolkit package.

All tool category modules are imported here so their ``@register_tool``
decorators run and populate the shared registry.  After every category
has registered its handlers we take a snapshot of the specs as
``TOOL_SPECS`` so downstream code can import a plain list.

Public API (also re-exported by ``fitness_agent.tools``):

* ``TOOL_SPECS`` — list of tool spec dicts (snapshot after all imports)
* ``execute_tool(conn, user_id, name, args)`` — call a registered tool
* ``tool_specs_for_prompt()`` — JSON string of all tool specs for the LLM prompt
"""

# Register every tool category.  Order does not matter for the registry
# (it is a dict keyed by tool name), but all imports must complete before
# TOOL_SPECS is built below.
from . import context  # noqa: F401
from . import knowledge_tools  # noqa: F401
from . import memory_tools  # noqa: F401
from . import plans  # noqa: F401
from . import reps  # noqa: F401
from . import todo_tools  # noqa: F401
from . import vision_tools  # noqa: F401
from . import web_tools  # noqa: F401
from . import workouts  # noqa: F401

from .registry import execute_registered_tool as execute_tool  # noqa: E402
from .registry import tool_specs, tool_specs_for_prompt  # noqa: E402

# Snapshot of all registered tool specs, taken once all categories have
# been imported.  Consumers can import ``TOOL_SPECS`` as a plain list.
TOOL_SPECS = tool_specs()

__all__ = [
    "TOOL_SPECS",
    "execute_tool",
    "tool_specs_for_prompt",
]
