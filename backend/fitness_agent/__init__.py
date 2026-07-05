"""Smart Fitness 专属健身 Agent 包。

对外保持旧接口兼容：
- detect_domains(message)
- respond(conn, user_id, message, mode="auto", history=None)
- nutrition_plan(conn, user_id, goal=...)
- INTENT_KEYWORDS

以后给 Agent 加能力，优先改这个目录里的文件，不要把逻辑继续塞进 main_v2_extra.py。
"""
from .core import (
    INTENT_KEYWORDS,
    detect_domains,
    nutrition_plan,
    respond as respond_once,
)
from .background import (
    dismiss_background_item,
    ensure_background_schema,
    list_background_items,
    mark_background_item_read,
    run_background_checks,
)
from .compact import (
    compact_trace,
    ensure_context_schema,
    get_context_summary,
    prepare_llm_history_with_summary,
    summarize_chat_history,
)
from .history import (
    add_agent_chat_message,
    delete_agent_chat_history,
    ensure_agent_chat_schema,
    get_agent_chat_history,
    to_llm_history,
)
from .hooks import clear_hooks, register_default_hooks, register_hook, trigger_hooks
from .knowledge_loader import load_catalog, public_catalog, search_knowledge
from .loop import respond_with_loop
from .memory import (
    MEMORY_KINDS,
    add_layered_memory,
    build_memory_snapshot,
    ensure_memory_schema,
    list_layered_memories,
    list_memories_by_kind,
    normalize_memory_kind,
)
from .permissions import (
    check_permission,
    create_approval,
    ensure_permission_schema,
    get_approval,
    list_pending_approvals,
    mark_approval,
)
from .registry import AGENT_DOMAINS, get_domain_catalog
from .runtime import get_run, list_runs, resume_run_after_approval, resume_run_after_denial, start_run
from .state import ensure_agent_state_schema, list_provider_health, recent_agent_stats
from .tools import TOOL_SPECS, execute_tool
from .web_search import search_fitness_web

# Keep the old public API name, but route chat through the new safe tool loop.
respond = respond_with_loop

__all__ = [
    "AGENT_DOMAINS",
    "INTENT_KEYWORDS",
    "MEMORY_KINDS",
    "TOOL_SPECS",
    "add_agent_chat_message",
    "add_layered_memory",
    "build_memory_snapshot",
    "check_permission",
    "clear_hooks",
    "compact_trace",
    "create_approval",
    "delete_agent_chat_history",
    "detect_domains",
    "dismiss_background_item",
    "ensure_agent_chat_schema",
    "ensure_agent_state_schema",
    "ensure_background_schema",
    "ensure_context_schema",
    "ensure_memory_schema",
    "ensure_permission_schema",
    "execute_tool",
    "get_agent_chat_history",
    "get_approval",
    "get_context_summary",
    "get_domain_catalog",
    "get_run",
    "list_background_items",
    "list_layered_memories",
    "list_memories_by_kind",
    "list_pending_approvals",
    "list_provider_health",
    "list_runs",
    "load_catalog",
    "mark_approval",
    "mark_background_item_read",
    "normalize_memory_kind",
    "nutrition_plan",
    "prepare_llm_history_with_summary",
    "public_catalog",
    "register_default_hooks",
    "recent_agent_stats",
    "register_hook",
    "respond",
    "respond_once",
    "respond_with_loop",
    "resume_run_after_approval",
    "resume_run_after_denial",
    "run_background_checks",
    "search_fitness_web",
    "search_knowledge",
    "start_run",
    "summarize_chat_history",
    "to_llm_history",
    "trigger_hooks",
]
