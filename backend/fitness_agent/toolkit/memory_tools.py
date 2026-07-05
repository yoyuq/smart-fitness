"""Coach memory tools."""
from typing import Any, Dict

from ..memory import add_layered_memory, build_memory_snapshot, list_layered_memories, normalize_memory_kind
from .base import clamp_int
from .registry import register_tool


@register_tool(
    name="get_coach_memory",
    description="按分层读取教练长期记忆。参数: limit 1-30, kinds 可选(goal/preference/injury/diet/training_pattern/observation/run_summary/general)。",
    args={"limit": "int optional, default 10", "kinds": "list optional"},
)
def get_coach_memory(conn, user_id: int, args: Dict[str, Any]) -> Dict[str, Any]:
    limit = clamp_int(args.get("limit"), 10, 1, 30)
    raw_kinds = args.get("kinds") or args.get("kind") or []
    if isinstance(raw_kinds, str):
        raw_kinds = [raw_kinds]
    kinds = [normalize_memory_kind(k) for k in raw_kinds if str(k).strip()] if isinstance(raw_kinds, list) else []
    return {"ok": True, "memories": list_layered_memories(conn, user_id, kinds=kinds, limit=limit), "kinds": sorted(set(kinds))}


@register_tool(
    name="get_memory_snapshot",
    description="按 goal/preference/injury/diet/training_pattern/observation/run_summary/general 分组读取长期记忆摘要。参数: limit_per_kind 1-10。",
    args={"limit_per_kind": "int optional, default 4"},
)
def get_memory_snapshot(conn, user_id: int, args: Dict[str, Any]) -> Dict[str, Any]:
    limit_per_kind = clamp_int(args.get("limit_per_kind"), 4, 1, 10)
    return {"ok": True, "memory": build_memory_snapshot(conn, user_id, limit_per_kind=limit_per_kind)}


@register_tool(
    name="save_coach_memory",
    description="保存分层长期记忆。修改类工具，执行前必须获得用户确认。参数: note, kind/category。kind 可为 goal/preference/injury/diet/training_pattern/observation/run_summary/general。",
    args={"note": "string", "kind": "goal|preference|injury|diet|training_pattern|observation|run_summary|general", "category": "optional legacy alias"},
    read_only=False,
    requires_approval=True,
)
def save_coach_memory(conn, user_id: int, args: Dict[str, Any]) -> Dict[str, Any]:
    note = (args.get("note") or "").strip()
    category = (args.get("kind") or args.get("category") or "general").strip()
    if len(note) < 4:
        return {"ok": False, "error": "note too short"}
    saved = add_layered_memory(
        conn,
        user_id,
        note,
        kind=category,
        source="approved_tool",
        confidence=0.95,
        run_id=args.get("run_id"),
        metadata={"tool": "save_coach_memory"},
    )
    return {"ok": True, "saved": saved}
