"""Layered long-term memory for the Smart Fitness Agent.

This module keeps compatibility with the existing ``coach_memory`` table while
adding a small typed-memory layer. The table remains append-only by default:
new facts are inserted only through write tools that pass the App approval
pipeline. Reads can filter by kind so the prompt gets concise, relevant memory.
"""
import json
import re
import sqlite3
import time
from typing import Any, Dict, Iterable, List, Optional

MEMORY_KINDS = {
    "goal",
    "preference",
    "injury",
    "diet",
    "training_pattern",
    "observation",
    "run_summary",
    "general",
}

KIND_ALIASES = {
    "objective": "goal",
    "target": "goal",
    "like": "preference",
    "pref": "preference",
    "pain": "injury",
    "hurt": "injury",
    "limitation": "injury",
    "nutrition": "diet",
    "meal": "diet",
    "food": "diet",
    "training": "training_pattern",
    "pattern": "training_pattern",
    "summary": "run_summary",
}

TABLE_SQL = """
CREATE TABLE IF NOT EXISTS coach_memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    category TEXT DEFAULT 'general',
    note TEXT NOT NULL,
    created_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_coach_memory_user_created
    ON coach_memory(user_id, created_at DESC);
"""


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def ensure_memory_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(TABLE_SQL)
    _ensure_column(conn, "coach_memory", "kind", "kind TEXT")
    _ensure_column(conn, "coach_memory", "source", "source TEXT DEFAULT 'agent'")
    _ensure_column(conn, "coach_memory", "confidence", "confidence REAL DEFAULT 1.0")
    _ensure_column(conn, "coach_memory", "active", "active INTEGER DEFAULT 1")
    _ensure_column(conn, "coach_memory", "run_id", "run_id TEXT")
    _ensure_column(conn, "coach_memory", "metadata_json", "metadata_json TEXT DEFAULT '{}'")
    conn.execute(
        "UPDATE coach_memory SET kind=COALESCE(kind, category, 'general') WHERE kind IS NULL OR kind=''"
    )
    conn.execute(
        "UPDATE coach_memory SET active=1 WHERE active IS NULL"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_coach_memory_user_kind_active ON coach_memory(user_id, kind, active, created_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_coach_memory_run_id ON coach_memory(run_id)")
    conn.commit()


def normalize_memory_kind(kind: Optional[str]) -> str:
    raw = (kind or "general").strip().lower().replace("-", "_").replace(" ", "_")
    raw = KIND_ALIASES.get(raw, raw)
    return raw if raw in MEMORY_KINDS else "general"


def infer_memory_kind(note: str, category: Optional[str] = None) -> str:
    explicit = normalize_memory_kind(category)
    if explicit != "general":
        return explicit
    text = (note or "").lower()
    zh = note or ""
    if any(w in text for w in ["goal", "target", "aim", "want to", "plan to"]) or any(w in zh for w in ["目标", "想要", "计划", "希望", "冲刺"]):
        return "goal"
    if any(w in text for w in ["prefer", "like", "dislike", "hate", "avoid"]) or any(w in zh for w in ["偏好", "喜欢", "不喜欢", "讨厌", "避免"]):
        return "preference"
    if any(w in text for w in ["injury", "pain", "hurt", "sore", "knee", "ankle", "shoulder"]) or any(w in zh for w in ["伤", "疼", "痛", "膝", "踝", "肩", "腰"]):
        return "injury"
    if any(w in text for w in ["diet", "meal", "protein", "carb", "calorie", "breakfast"]) or any(w in zh for w in ["饮食", "食堂", "蛋白", "碳水", "热量", "早餐", "午餐", "晚餐"]):
        return "diet"
    if any(w in text for w in ["run", "workout", "training", "squat", "push-up", "pull-up"]) or any(w in zh for w in ["训练", "跑步", "深蹲", "俯卧撑", "引体", "动作模式", "习惯"]):
        return "training_pattern"
    return "observation"


def _safe_float(value: Any, default: float = 1.0, lo: float = 0.0, hi: float = 1.0) -> float:
    try:
        v = float(value)
    except Exception:
        v = default
    return max(lo, min(v, hi))


def _parse_json(raw: Any, default: Any):
    if not raw:
        return default
    try:
        return json.loads(raw)
    except Exception:
        return default


def _row_to_memory(row) -> Dict[str, Any]:
    if isinstance(row, sqlite3.Row):
        keys = set(row.keys())
        note = row["note"]
        kind = row["kind"] if "kind" in keys and row["kind"] else row["category"]
        return {
            "id": row["id"],
            "category": row["category"],
            "kind": normalize_memory_kind(kind),
            "note": note,
            "source": row["source"] if "source" in keys else "agent",
            "confidence": float(row["confidence"] if "confidence" in keys and row["confidence"] is not None else 1.0),
            "active": bool(row["active"] if "active" in keys else 1),
            "run_id": row["run_id"] if "run_id" in keys else None,
            "metadata": _parse_json(row["metadata_json"] if "metadata_json" in keys else None, {}),
            "created_at": row["created_at"],
        }
    # Tuple fallback: support both legacy 4-column selects and the 10-column
    # selects used by this module when row_factory is not sqlite3.Row.
    if len(row) >= 10:
        return {
            "id": row[0],
            "category": row[1],
            "kind": normalize_memory_kind(row[2] or row[1]),
            "note": row[3],
            "created_at": row[4],
            "source": row[5] or "agent",
            "confidence": float(row[6] if row[6] is not None else 1.0),
            "active": bool(row[7]),
            "run_id": row[8],
            "metadata": _parse_json(row[9], {}),
        }
    return {"id": row[0], "category": row[1], "kind": normalize_memory_kind(row[1]), "note": row[2], "created_at": row[3], "source": "agent", "confidence": 1.0, "active": True, "run_id": None, "metadata": {}}


def add_layered_memory(
    conn: sqlite3.Connection,
    user_id: int,
    note: str,
    kind: str = "general",
    *,
    source: str = "agent",
    confidence: float = 1.0,
    run_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    dedupe: bool = True,
) -> Dict[str, Any]:
    ensure_memory_schema(conn)
    clean = re.sub(r"\s+", " ", (note or "").strip())[:500]
    if len(clean) < 4:
        raise ValueError("memory note too short")
    memory_kind = infer_memory_kind(clean, kind)
    if dedupe:
        existing = conn.execute(
            "SELECT id, category, kind, note, created_at, source, confidence, active, run_id, metadata_json "
            "FROM coach_memory WHERE user_id=? AND active=1 AND note=? ORDER BY id DESC LIMIT 1",
            (user_id, clean),
        ).fetchone()
        if existing:
            return {**_row_to_memory(existing), "deduped": True}
    now = int(time.time())
    conn.execute(
        """
        INSERT INTO coach_memory (user_id, category, kind, note, created_at, source, confidence, active, run_id, metadata_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
        """,
        (
            user_id,
            memory_kind,
            memory_kind,
            clean,
            now,
            (source or "agent")[:40],
            _safe_float(confidence),
            run_id,
            json.dumps(metadata or {}, ensure_ascii=False, default=str),
        ),
    )
    conn.commit()
    row = conn.execute(
        "SELECT id, category, kind, note, created_at, source, confidence, active, run_id, metadata_json "
        "FROM coach_memory WHERE user_id=? ORDER BY id DESC LIMIT 1",
        (user_id,),
    ).fetchone()
    return _row_to_memory(row)


def list_layered_memories(
    conn: sqlite3.Connection,
    user_id: int,
    *,
    kinds: Optional[Iterable[str]] = None,
    limit: int = 10,
    active_only: bool = True,
) -> List[Dict[str, Any]]:
    ensure_memory_schema(conn)
    limit = max(1, min(int(limit or 10), 100))
    filters = ["user_id=?"]
    vals: List[Any] = [user_id]
    if active_only:
        filters.append("COALESCE(active,1)=1")
    norm_kinds = [normalize_memory_kind(k) for k in (kinds or []) if normalize_memory_kind(k)]
    norm_kinds = [k for k in norm_kinds if k in MEMORY_KINDS]
    if norm_kinds:
        placeholders = ",".join("?" for _ in norm_kinds)
        filters.append(f"COALESCE(kind, category, 'general') IN ({placeholders})")
        vals.extend(norm_kinds)
    vals.append(limit)
    rows = conn.execute(
        "SELECT id, category, kind, note, created_at, source, confidence, active, run_id, metadata_json "
        f"FROM coach_memory WHERE {' AND '.join(filters)} ORDER BY created_at DESC, id DESC LIMIT ?",
        vals,
    ).fetchall()
    return [_row_to_memory(r) for r in rows]


def list_memories_by_kind(conn: sqlite3.Connection, user_id: int, *, limit_per_kind: int = 5) -> Dict[str, List[Dict[str, Any]]]:
    ensure_memory_schema(conn)
    out: Dict[str, List[Dict[str, Any]]] = {}
    for kind in ["goal", "preference", "injury", "diet", "training_pattern", "observation", "run_summary", "general"]:
        rows = list_layered_memories(conn, user_id, kinds=[kind], limit=limit_per_kind)
        if rows:
            out[kind] = rows
    return out


def build_memory_snapshot(conn: sqlite3.Connection, user_id: int, *, limit_per_kind: int = 4) -> Dict[str, Any]:
    by_kind = list_memories_by_kind(conn, user_id, limit_per_kind=limit_per_kind)
    total = sum(len(v) for v in by_kind.values())
    return {"kinds": sorted(MEMORY_KINDS), "total_returned": total, "by_kind": by_kind}


def add_run_summary_memory(
    conn: sqlite3.Connection,
    user_id: int,
    run_id: str,
    summary: str,
    *,
    confidence: float = 0.8,
) -> Dict[str, Any]:
    return add_layered_memory(
        conn,
        user_id,
        summary,
        "run_summary",
        source="run_summary",
        confidence=confidence,
        run_id=run_id,
        metadata={"auto_generated": True},
    )
