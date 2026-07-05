"""Shared helpers for Smart Fitness Agent tools."""
import sqlite3
from typing import Any, Dict, List


def clamp_int(value: Any, default: int, lo: int, hi: int) -> int:
    try:
        n = int(value)
    except Exception:
        n = default
    return max(lo, min(n, hi))


def rows_to_dicts(rows) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for r in rows:
        if isinstance(r, sqlite3.Row):
            out.append({k: r[k] for k in r.keys()})
        else:
            out.append(dict(r))
    return out
