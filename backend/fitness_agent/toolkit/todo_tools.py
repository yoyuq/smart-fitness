"""Run-local todo planning tool."""
from typing import Any, Dict

from ..todos import run_todo_write
from .registry import register_tool


@register_tool(
    name="todo_write",
    description="为复杂健身任务创建/更新带状态的 TODO 列表，只修改本次 Agent run 的规划状态，不修改用户健身数据。状态: pending/in_progress/waiting_approval/completed/cancelled。",
    args={"todos": "list of {content,status}"},
)
def todo_write(conn, user_id: int, args: Dict[str, Any]) -> Dict[str, Any]:
    return run_todo_write(args)
