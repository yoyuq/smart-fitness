"""Tiny in-process scheduler for Smart Fitness Agent background checks.

The durable logic lives in fitness_agent.background. This module only decides
when to call it while the FastAPI backend process is running. It is deliberately
best-effort: if the backend is down, jobs simply run on the next process start.
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from typing import Callable, Optional

from .background import run_background_checks
from .config import getenv

log = logging.getLogger("fitness_agent.background_scheduler")

_TASK: Optional[asyncio.Task] = None
_STOP: Optional[asyncio.Event] = None


def _enabled() -> bool:
    return getenv("AI_AGENT_BACKGROUND_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}


def _interval_seconds() -> int:
    try:
        return max(60, int(getenv("AI_AGENT_BACKGROUND_INTERVAL_SEC", "1800")))
    except Exception:
        return 1800


def _user_ids(conn: sqlite3.Connection) -> list[int]:
    rows = conn.execute("SELECT id FROM users ORDER BY id LIMIT 200").fetchall()
    ids = []
    for r in rows:
        try:
            ids.append(int(r["id"] if hasattr(r, "keys") else r[0]))
        except Exception:
            continue
    return ids


def _run_once(db_path: str) -> dict:
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    now = int(time.time())
    created = 0
    users = 0
    try:
        for user_id in _user_ids(conn):
            users += 1
            for job in ("daily_checkin", "weekly_review"):
                try:
                    res = run_background_checks(conn, user_id, job=job, now=now)
                    created += int(res.get("created") or 0)
                except Exception as exc:
                    log.warning("background job failed user=%s job=%s: %s", user_id, job, exc)
        return {"ok": True, "users": users, "created": created}
    finally:
        conn.close()


async def _loop(db_path: str, interval: int, stop_event: asyncio.Event) -> None:
    # Small delay lets startup DB migrations/routes settle.
    await asyncio.sleep(5)
    while not stop_event.is_set():
        try:
            result = await asyncio.to_thread(_run_once, db_path)
            if result.get("created"):
                log.info("Fitness Agent background created %s item(s) for %s user(s)", result.get("created"), result.get("users"))
        except Exception as exc:
            log.warning("Fitness Agent background scheduler tick failed: %s", exc)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


def start_background_scheduler(db_path: str, *, interval_sec: Optional[int] = None) -> bool:
    """Start the in-process scheduler if enabled.

    Returns True when a scheduler task is running/started.
    """
    global _TASK, _STOP
    if not _enabled():
        return False
    if _TASK and not _TASK.done():
        return True
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return False
    _STOP = asyncio.Event()
    _TASK = loop.create_task(_loop(db_path, int(interval_sec or _interval_seconds()), _STOP))
    return True


async def stop_background_scheduler() -> None:
    global _TASK, _STOP
    if _STOP:
        _STOP.set()
    task = _TASK
    if task and not task.done():
        try:
            await asyncio.wait_for(task, timeout=3)
        except Exception:
            task.cancel()
    _TASK = None
    _STOP = None
