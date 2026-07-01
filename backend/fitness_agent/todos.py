"""Todo planning support for Smart Fitness Agent runs."""
from typing import Any, Dict, List, Optional

_ALLOWED = {"pending", "in_progress", "waiting_approval", "completed", "cancelled"}


def normalize_todos(raw: Any) -> List[Dict[str, str]]:
    todos: List[Dict[str, str]] = []
    if not isinstance(raw, list):
        return todos
    in_progress_seen = False
    for item in raw[:20]:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or "").strip()[:160]
        if not content:
            continue
        status = str(item.get("status") or "pending").strip()
        if status not in _ALLOWED:
            status = "pending"
        if status == "in_progress":
            if in_progress_seen:
                status = "pending"
            in_progress_seen = True
        todos.append({"content": content, "status": status})
    return todos


def run_todo_write(args: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    args = args or {}
    todos = normalize_todos(args.get("todos"))
    return {"ok": True, "todos": todos, "message": f"Updated {len(todos)} todos"}
